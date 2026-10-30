import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import gemini_public_link_routes as routes
import knowledge_store
from knowledge_routes import create_access_token, require_admin
from public_link_routes import PublicLinkExtractionMetadata, ValidatedPublicURL


URL = "https://example.com/"
DOCUMENT = "Example domain\n\nPurpose\nThis domain is intended for documentation examples."
RESULT = {
    "candidates": [{
        "finishReason": "STOP",
        "content": {"parts": [{"text": DOCUMENT}]},
        "urlContextMetadata": {"urlMetadata": [{
            "retrievedUrl": URL, "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS",
        }]},
    }],
    "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 20, "totalTokenCount": 220},
}


class GeminiPublicLinkTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[require_admin] = lambda: {"role": "Admin"}
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.key = patch.object(routes.os, "getenv", return_value="mock-key").start()
        self.redirect = patch.object(routes, "resolve_public_redirects", return_value=URL).start()
        self.validate = patch.object(routes, "validate_public_url", return_value=ValidatedPublicURL(
            URL, "example.com", 443, "https", ("93.184.216.34",),
        )).start()
        self.provider = patch.object(routes, "_request_gemini", return_value=copy.deepcopy(RESULT)).start()
        self.firecrawl = patch("public_link_routes._create_firecrawl_client").start()
        self.addCleanup(patch.stopall)

    def preview(self):
        return self.client.post("/api/knowledge/public-link/preview", json={"url": URL})

    def test_preview_single_url_context_call_without_firecrawl_or_storage(self):
        with patch.object(
            knowledge_store, "save_text", side_effect=AssertionError("No auto-save")
        ), patch.object(
            knowledge_store, "initialize_database", side_effect=AssertionError("Preview cannot initialize storage")
        ), patch("openai.OpenAI") as groq:
            response = self.preview()
        groq.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["metadata"]["model"], "gemini-3.5-flash")
        self.assertEqual(body["generated_content"], DOCUMENT)
        self.assertEqual(body["cleaned_content"], "")
        self.assertFalse(body["metadata"]["source_text_available"])
        metrics = body["metadata"]["generation_metrics"]
        self.assertEqual(metrics["call_count"], 1)
        self.assertEqual(metrics["source_chars"], 0)
        self.assertEqual(metrics["actual_input_tokens"], 200)
        self.assertEqual(metrics["actual_output_tokens"], 20)
        self.assertEqual(metrics["usage_status"], "available")
        self.assertIsNone(metrics["estimated_input_tokens"])
        self.provider.assert_called_once()
        payload = self.provider.call_args.args[1]
        self.assertEqual(payload["tools"], [{"url_context": {}}])
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 4096)
        self.assertEqual(payload["generationConfig"]["candidateCount"], 1)
        self.assertNotIn("thinkingConfig", payload["generationConfig"])
        self.firecrawl.assert_not_called()
        self.redirect.assert_called_once_with(URL)
        self.validate.assert_called_once_with(URL)

    def test_retrieval_failure_missing_metadata_or_unexpected_url_rejected(self):
        for entries in (None, [], [{"retrievedUrl": URL, "urlRetrievalStatus": "FAILED"}], [{
            "retrievedUrl": "https://other.example.org/", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS",
        }]):
            with self.subTest(entries=entries):
                result = copy.deepcopy(RESULT)
                result["candidates"][0]["urlContextMetadata"] = {"urlMetadata": entries}
                self.provider.return_value = result
                self.assertEqual(self.preview().status_code, 422)

    def test_incomplete_blocked_empty_or_malformed_output_rejected(self):
        for reason in ("MAX_TOKENS", "SAFETY", None):
            with self.subTest(reason=reason):
                result = copy.deepcopy(RESULT)
                result["candidates"][0]["finishReason"] = reason
                self.provider.return_value = result
                self.assertEqual(self.preview().status_code, 422)
        for result in ({}, {"candidates": [None]}, {"candidates": []}):
            self.provider.return_value = result
            self.assertEqual(self.preview().status_code, 502)
        for parts in ([], [{"text": ""}], [{"functionCall": {}}]):
            result = copy.deepcopy(RESULT)
            result["candidates"][0]["content"]["parts"] = parts
            self.provider.return_value = result
            self.assertIn(self.preview().status_code, (422, 502))

    def test_generated_document_limit_is_not_silently_truncated(self):
        result = copy.deepcopy(RESULT)
        for size, status in ((200000, 200), (200001, 413)):
            result["candidates"][0]["content"]["parts"] = [{"text": "x" * size}]
            self.provider.return_value = result
            self.assertEqual(self.preview().status_code, status)

    def test_provider_failure_has_no_automatic_retry(self):
        self.provider.side_effect = HTTPException(502, "Provider failed")
        self.assertEqual(self.preview().status_code, 502)
        self.provider.assert_called_once()

    def test_url_security_failure_prevents_provider_call(self):
        self.redirect.side_effect = HTTPException(422, "Non-public URL")
        self.assertEqual(self.preview().status_code, 422)
        self.provider.assert_not_called()

    def test_missing_configuration_prevents_provider_call(self):
        self.key.return_value = ""
        self.assertEqual(self.preview().status_code, 503)
        self.provider.assert_not_called()

    def test_authentication_required_for_preview_and_save(self):
        self.app.dependency_overrides.clear()
        for path, data in (
            ("/preview", {"url": URL}), ("", {"url": URL, "text": DOCUMENT}),
        ):
            self.assertEqual(self.client.post("/api/knowledge/public-link" + path, json=data).status_code, 401)
        self.provider.assert_not_called()

    def test_employee_cannot_preview_or_save(self):
        self.app.dependency_overrides.clear()
        with patch("knowledge_routes._session_secret", return_value=b"test-only-session-secret"):
            headers = {"Authorization": f"Bearer {create_access_token('employee@example.test', 'Employee')}"}
            for path, data in (
                ("/preview", {"url": URL}), ("", {"url": URL, "text": DOCUMENT}),
            ):
                response = self.client.post("/api/knowledge/public-link" + path, json=data, headers=headers)
                self.assertEqual(response.status_code, 403)
        self.provider.assert_not_called()
        self.firecrawl.assert_not_called()

    def test_resolved_validated_url_is_the_only_url_submitted(self):
        final_url = "https://example.com/article"
        self.redirect.return_value = final_url
        self.validate.return_value = ValidatedPublicURL(
            final_url, "example.com", 443, "https", ("93.184.216.34",),
        )
        result = copy.deepcopy(RESULT)
        result["candidates"][0]["urlContextMetadata"]["urlMetadata"][0]["retrievedUrl"] = final_url
        self.provider.return_value = result
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.text)
        self.validate.assert_called_once_with(final_url)
        self.assertEqual(self.provider.call_args.args[1]["contents"], [
            {"role": "user", "parts": [{"text": f"Source URL: {final_url}"}]},
        ])
        self.assertEqual(response.json()["source_url"], final_url)

    def test_usage_missing_invalid_partial_and_thinking_total(self):
        for usage, status in (
            ({}, "missing"),
            ({"promptTokenCount": 40}, "partial"),
            ({"promptTokenCount": True}, "invalid"),
            ({"promptTokenCount": 40, "candidatesTokenCount": 10, "totalTokenCount": 1}, "invalid"),
            ({"promptTokenCount": 40, "candidatesTokenCount": 10, "totalTokenCount": 80}, "available"),
        ):
            with self.subTest(usage=usage):
                metrics = routes._usage_metrics({"usageMetadata": usage}, 1, 100)
                self.assertEqual(metrics.usage_status, status)
                self.assertIsNone(metrics.estimated_input_tokens)

    def test_reviewed_edit_saves_to_existing_storage_and_is_searchable(self):
        preview = self.preview().json()
        reviewed = DOCUMENT + "\nAdminverified instructions."
        with tempfile.TemporaryDirectory() as directory, patch.object(
            knowledge_store, "DATABASE_PATH", Path(directory) / "knowledge.db"
        ), patch("public_link_routes.validate_public_url", return_value=self.validate.return_value):
            response = self.client.post("/api/knowledge/public-link", json={
                "url": URL, "text": reviewed, "metadata": preview["metadata"],
            })
            self.assertEqual(response.status_code, 200, response.text)
            source = knowledge_store.get_source(response.json()["source"]["id"])
            self.assertEqual(source["original_text"], reviewed)
            self.assertEqual(source["source_type"], "Public Link")
            self.assertEqual(source["metadata"]["provider"], "gemini")
            self.assertEqual(
                PublicLinkExtractionMetadata(**source["metadata"]).generation_metrics.outcome, "generated"
            )
            self.assertTrue(knowledge_store.search_chunks("Adminverified"))

    def test_transport_handles_network_and_http_failures_without_leaking_details(self):
        with patch.object(routes, "urlopen", side_effect=URLError("sensitive detail")):
            with self.assertRaises(HTTPException) as raised:
                self.transport("key", {})
            self.assertNotIn("sensitive", raised.exception.detail)
        for status in (401, 429, 500):
            with patch.object(routes, "urlopen", side_effect=HTTPError(URL, status, "private", {}, None)):
                with self.assertRaises(HTTPException) as raised:
                    self.transport("key", {})
                self.assertEqual(raised.exception.status_code, {401: 503, 429: 429, 500: 502}[status])

    def test_provider_error_categories_do_not_expose_provider_text(self):
        for code, reason, message, expected in (
            (400, "API_KEY_INVALID", "private", "API key is invalid"),
            (403, "SERVICE_DISABLED", "private", "Enable the Generative Language API"),
            (400, "", "User location is not supported private", "region"),
            (403, "", "private", "denied access"),
            (404, "", "private", "unavailable"),
            (400, "", "Unknown name private", "request configuration"),
        ):
            error = HTTPError(URL, code, "private", {}, io.BytesIO(json.dumps({
                "error": {"message": message, "details": [{"reason": reason}]},
            }).encode()))
            with self.subTest(code=code, reason=reason), self.assertLogs("gemini_public_link_routes", level="WARNING") as logs:
                result = routes._provider_error(error)
            self.assertIn(expected, result.detail)
            self.assertNotIn("private", result.detail)
            self.assertNotIn("private", "".join(logs.output))

    def test_transport_response_limits_and_json_validation(self):
        for data, status in (
            (b"{}", None), (b"invalid JSON", 502), (b"[]", 502),
            (b"x" * (routes.MAX_RESPONSE_BYTES + 1), 502),
        ):
            with self.subTest(status=status), patch.object(routes, "urlopen", return_value=io.BytesIO(data)):
                if status is None:
                    self.assertEqual(self.transport("mock-key", {}), {})
                else:
                    with self.assertRaises(HTTPException) as raised:
                        self.transport("mock-key", {})
                    self.assertEqual(raised.exception.status_code, status)

    def test_rest_transport_uses_verified_model_and_url_context_tool(self):
        self.preview()
        payload = self.provider.call_args.args[1]
        with patch.object(routes, "urlopen", return_value=io.BytesIO(b"{}")) as transport:
            self.transport("test-backend-key", payload)
        request = transport.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-goog-api-key"), "test-backend-key")
        self.assertNotIn("test-backend-key", request.full_url)
        self.assertEqual(json.loads(request.data)["tools"], [{"url_context": {}}])
        self.assertEqual(transport.call_args.kwargs["timeout"], 90)

    transport = staticmethod(routes._request_gemini)


if __name__ == "__main__":
    unittest.main()
