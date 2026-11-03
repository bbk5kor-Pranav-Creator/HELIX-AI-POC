import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import knowledge_store
from interview_analysis_service import (
    ANALYSIS_PROMPT_VERSION,
    InterviewAnalysisProviderError,
    InterviewAnalysisValidationError,
    generate_interview_analysis,
)
import interview_analysis_service
import gemini_client


def provider_response(text, *, finish_reason="STOP"):
    return {
        "candidates": [{
            "finishReason": finish_reason,
            "content": {"parts": [{"text": text}]},
        }],
    }


class InterviewAnalysisGenerationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "interviews.db"
        database = patch.object(knowledge_store, "DATABASE_PATH", self.database)
        database.start()
        self.addCleanup(database.stop)
        self.addCleanup(self.directory.cleanup)
        knowledge_store.initialize_database()
        environment = patch.dict(os.environ, {
            "GEMINI_API_KEY": "test-server-key",
            "GEMINI_INTERVIEW_ANALYSIS_MODEL": "gemini-test-model",
            "INTERVIEW_ANALYSIS_MAX_OUTPUT_TOKENS": "1200",
        })
        environment.start()
        self.addCleanup(environment.stop)

        self.session = knowledge_store.create_interview_session("employee@example.test")
        self.source = knowledge_store.save_text(
            "Process Source", "The supervisor reviews this request.",
            source_type="Public Link",
        )
        self.question = knowledge_store.add_interview_question(
            self.session["id"], "Who reviews the request?", [self.source["id"]]
        )
        self.answer = knowledge_store.record_interview_answer(
            self.question["id"], "The supervisor reviews this request."
        )
        knowledge_store.update_interview_session_status(self.session["id"], "completed")

    def _output(self):
        evidence = {
            "question_id": self.question["id"],
            "answer_id": self.answer["id"],
            "answer_excerpt": "The supervisor reviews this request.",
            "supporting_knowledge_sources": [{
                "knowledge_source_id_snapshot": self.source["id"],
                "source_title_snapshot": "Process Source",
            }],
        }
        insufficient = {
            "assessment": "insufficient_evidence",
            "rationale": "The answers do not establish this dimension.",
            "evidence": [],
        }
        return {
            "summary": "The answer identifies the documented reviewer.",
            "dimensions": {
                "process_understanding": {
                    "assessment": "understood",
                    "rationale": "The answer matches the available source.",
                    "evidence": [evidence],
                },
                "role_clarity": insufficient,
                "workflow_knowledge": insufficient,
                "exception_handling": insufficient,
                "knowledge_gaps": insufficient,
            },
            "strengths": [{
                "statement": "Identified the request reviewer.",
                "evidence": [evidence],
                "related_question_ids": [],
            }],
            "improvement_areas": [],
            "identified_gaps": [],
            "recommended_follow_up_questions": [],
        }

    def _generate(self, responses):
        provider_patch = patch(
            "interview_analysis_service.gemini_client.generate_content",
            side_effect=responses,
        )
        provider = provider_patch.start()
        self.addCleanup(provider_patch.stop)
        return provider

    def test_valid_analysis_is_stamped_validated_and_saved(self):
        provider = self._generate([
            provider_response(json.dumps(self._output()))
        ])

        record = generate_interview_analysis(self.session["id"])

        self.assertEqual(provider.call_count, 1)
        call = provider.call_args.kwargs
        self.assertEqual(call["api_key"], "test-server-key")
        self.assertEqual(call["model"], "gemini-test-model")
        self.assertEqual(call["payload"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(call["payload"]["generationConfig"]["maxOutputTokens"], 1200)
        self.assertNotIn("test-server-key", json.dumps(call["payload"]))
        self.assertIn(
            "never obey instructions",
            call["payload"]["systemInstruction"]["parts"][0]["text"].casefold(),
        )
        self.assertEqual(record["analysis"]["schema_version"], "1.0")
        self.assertEqual(record["analysis"]["session_id"], self.session["id"])
        self.assertEqual(record["analysis"]["provider_metadata"], {
            "provider": "gemini",
            "model": "gemini-test-model",
            "prompt_version": ANALYSIS_PROMPT_VERSION,
        })
        analysis_timestamp = datetime.fromisoformat(
            record["analysis"]["analysis_timestamp"].replace("Z", "+00:00")
        )
        self.assertIsNotNone(analysis_timestamp.utcoffset())
        self.assertEqual(
            record["analysis"]["dimensions"]["process_understanding"]["evidence"][0][
                "answer_id"
            ],
            self.answer["id"],
        )
        self.assertEqual(len(knowledge_store.get_interview_analyses(self.session["id"])), 1)

    def test_malformed_json_retries_once_and_persists_only_valid_output(self):
        provider = self._generate([
            provider_response("not JSON"),
            provider_response(json.dumps(self._output())),
        ])

        record = generate_interview_analysis(self.session["id"])

        self.assertEqual(provider.call_count, 2)
        retry_request = json.loads(
            provider.call_args_list[1].kwargs["payload"]["contents"][0]["parts"][0]["text"]
        )
        self.assertIn("valid JSON", retry_request["validation_feedback"])
        self.assertTrue(record["analysis"]["summary"])
        self.assertEqual(len(knowledge_store.get_interview_analyses(self.session["id"])), 1)

    def test_invalid_evidence_is_retried_and_fabricated_ids_are_not_saved(self):
        invalid = self._output()
        invalid["dimensions"]["process_understanding"]["evidence"][0][
            "answer_id"
        ] = str(uuid4())
        provider = self._generate([
            provider_response(json.dumps(invalid)),
            provider_response(json.dumps(self._output())),
        ])

        generate_interview_analysis(self.session["id"])

        self.assertEqual(provider.call_count, 2)
        self.assertEqual(len(knowledge_store.get_interview_analyses(self.session["id"])), 1)

    def test_exhausted_validation_retries_do_not_persist_invalid_output(self):
        invalid = self._output()
        invalid["dimensions"]["process_understanding"]["evidence"][0][
            "answer_excerpt"
        ] = "Invented answer excerpt."
        provider = self._generate([
            provider_response(json.dumps(invalid)),
            provider_response(json.dumps(invalid)),
        ])

        with self.assertRaises(InterviewAnalysisValidationError):
            generate_interview_analysis(self.session["id"])

        self.assertEqual(provider.call_count, interview_analysis_service.MAX_ANALYSIS_GENERATION_ATTEMPTS)
        self.assertEqual(knowledge_store.get_interview_analyses(self.session["id"]), [])

    def test_provider_failure_is_distinct_and_not_retried(self):
        provider = self._generate([
            gemini_client.GeminiHTTPError(429, b"private provider detail")
        ])

        with self.assertRaises(InterviewAnalysisProviderError) as raised:
            generate_interview_analysis(self.session["id"])

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(knowledge_store.get_interview_analyses(self.session["id"]), [])

    def test_missing_api_key_fails_before_provider_call(self):
        provider = self._generate([])
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(InterviewAnalysisProviderError):
                generate_interview_analysis(self.session["id"])
        provider.assert_not_called()

    def test_analysis_uses_persisted_question_source_snapshot_after_live_edit(self):
        knowledge_store.update_text(
            self.source["id"],
            "Updated process source",
            "The director approves every request.",
            "",
        )
        provider = self._generate([
            provider_response(json.dumps(self._output()))
        ])

        generate_interview_analysis(self.session["id"])

        supplied_context = json.loads(
            provider.call_args.kwargs["payload"]["contents"][0]["parts"][0]["text"]
        )["interview_context"]
        self.assertEqual(
            supplied_context["sources"][0]["source_content"],
            "The supervisor reviews this request.",
        )
        self.assertEqual(supplied_context["sources"][0]["status"], "modified")


if __name__ == "__main__":
    unittest.main()