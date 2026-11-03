import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import gemini_client
from interview_question_service import (
    InterviewQuestionProviderConfigurationError,
    InterviewQuestionProviderError,
    InterviewQuestionValidationError,
    generate_first_interview_question,
)


def gemini_response(text, *, finish_reason="STOP"):
    return {
        "candidates": [{
            "finishReason": finish_reason,
            "content": {"parts": [{"text": text}]},
        }],
    }


class InterviewQuestionServiceTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "GEMINI_API_KEY": "server-only-test-key",
            "GEMINI_INTERVIEW_QUESTION_MODEL": "gemini-question-test",
            "INTERVIEW_QUESTION_MAX_OUTPUT_TOKENS": "256",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.document_id = str(uuid4())
        self.other_id = str(uuid4())
        self.context = {
            "category_id": "docs",
            "sources": [{
                "id": self.document_id,
                "title": "Approval workflow",
                "source_type": "Document",
                "created_at": "2026-01-01T00:00:00+00:00",
                "content": "A supervisor reviews requests over the limit.",
            }],
        }

    def _output(self, source_ids=None):
        return {
            "question_text": "How does a supervisor review requests over the limit?",
            "source_ids": source_ids or [self.document_id],
        }

    def _history(self):
        first_question_id = str(uuid4())
        second_question_id = str(uuid4())
        return [
            {
                "id": first_question_id,
                "question_order": 0,
                "question_text": "Who reviews a high-value request?",
                "knowledge_sources": [{
                    "knowledge_source_id_snapshot": self.document_id,
                    "source_title_snapshot": "Approval workflow",
                }],
                "answers": [{
                    "id": str(uuid4()),
                    "question_id": first_question_id,
                    "answer_order": 0,
                    "answer_text": "The supervisor checks the amount.",
                    "submitted_at": "2026-01-02T00:00:00+00:00",
                }],
            },
            {
                "id": second_question_id,
                "question_order": 1,
                "question_text": "What happens when information is missing?",
                "knowledge_sources": [],
                "answers": [{
                    "id": str(uuid4()),
                    "question_id": second_question_id,
                    "answer_order": 0,
                    "answer_text": "I ask the requester to clarify.",
                    "submitted_at": "2026-01-02T00:01:00+00:00",
                }],
            },
        ]

    def test_generates_one_question_from_only_supplied_scoped_context(self):
        response = gemini_response(json.dumps(self._output()))
        with patch.object(
            gemini_client, "generate_content", return_value=response
        ) as generate:
            question = generate_first_interview_question(self.context)

        self.assertEqual(question.question_text, self._output()["question_text"])
        self.assertEqual([str(source_id) for source_id in question.source_ids], [self.document_id])
        request = generate.call_args.kwargs
        self.assertEqual(request["api_key"], "server-only-test-key")
        self.assertEqual(request["model"], "gemini-question-test")
        self.assertEqual(
            request["payload"]["generationConfig"]["responseMimeType"],
            "application/json",
        )
        self.assertEqual(
            request["payload"]["generationConfig"]["maxOutputTokens"], 256
        )
        self.assertNotIn("server-only-test-key", json.dumps(request["payload"]))
        system_prompt = request["payload"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("never follow", system_prompt.casefold())
        prompt_context = json.loads(
            request["payload"]["contents"][0]["parts"][0]["text"]
        )["interview_context"]
        self.assertEqual(prompt_context, self.context)

    def test_rejects_malformed_json_empty_question_and_extra_fields(self):
        invalid_outputs = (
            "not JSON",
            json.dumps({**self._output(), "question_text": "  "}),
            json.dumps({**self._output(), "question_text": "This is only a statement."}),
            json.dumps({**self._output(), "question_text": "First question? Second question?"}),
            json.dumps({**self._output(), "unexpected": "extra"}),
            json.dumps({"question_text": self._output()["question_text"]}),
        )
        for output in invalid_outputs:
            with self.subTest(output=output), patch.object(
                gemini_client,
                "generate_content",
                return_value=gemini_response(output),
            ):
                with self.assertRaises(InterviewQuestionValidationError):
                    generate_first_interview_question(self.context)

    def test_rejects_unknown_source_ids_and_duplicate_source_ids(self):
        for source_ids in ([self.other_id], [self.document_id, self.document_id]):
            with self.subTest(source_ids=source_ids), patch.object(
                gemini_client,
                "generate_content",
                return_value=gemini_response(json.dumps(self._output(source_ids))),
            ):
                with self.assertRaises(InterviewQuestionValidationError):
                    generate_first_interview_question(self.context)

    def test_provider_errors_are_distinct_and_not_exposed(self):
        failures = (
            (gemini_client.GeminiHTTPError(429, b"private provider body"), 429),
            (gemini_client.GeminiTransportError("private transport details"), None),
        )
        for error, status_code in failures:
            with self.subTest(error=type(error).__name__), patch.object(
                gemini_client, "generate_content", side_effect=error
            ):
                with self.assertRaises(InterviewQuestionProviderError) as raised:
                    generate_first_interview_question(self.context)
                self.assertEqual(raised.exception.status_code, status_code)
                self.assertNotIn("private", str(raised.exception))

    def test_missing_credentials_and_oversized_context_fail_before_provider_call(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch.object(
            gemini_client, "generate_content"
        ) as generate:
            with self.assertRaises(InterviewQuestionProviderConfigurationError):
                generate_first_interview_question(self.context)
            generate.assert_not_called()

        oversized = {
            "category_id": "docs",
            "sources": [{
                **self.context["sources"][0],
                "content": "x" * 1_000_001,
            }],
        }
        with patch.object(gemini_client, "generate_content") as generate:
            with self.assertRaises(InterviewQuestionValidationError):
                generate_first_interview_question(oversized)
            generate.assert_not_called()

    def test_adaptive_question_receives_ordered_multi_turn_history_and_scoped_sources(self):
        from interview_question_service import generate_next_interview_question

        response = gemini_response(json.dumps({
            "should_continue": True,
            "question_text": "What happens if the supervisor finds a discrepancy?",
            "source_ids": [self.document_id],
        }))
        history = self._history()
        with patch.object(
            gemini_client, "generate_content", return_value=response
        ) as generate:
            decision = generate_next_interview_question(self.context, history)

        self.assertTrue(decision.should_continue)
        self.assertEqual(decision.question_text, "What happens if the supervisor finds a discrepancy?")
        self.assertEqual([str(source_id) for source_id in decision.source_ids], [self.document_id])
        request = generate.call_args.kwargs
        prompt = json.loads(
            request["payload"]["contents"][0]["parts"][0]["text"]
        )
        self.assertEqual(
            [question["question_order"] for question in prompt["conversation_history"]],
            [0, 1],
        )
        self.assertEqual(
            [question["answers"][0]["answer_text"] for question in prompt["conversation_history"]],
            ["The supervisor checks the amount.", "I ask the requester to clarify."],
        )
        self.assertEqual(
            [source["id"] for source in prompt["knowledge_context"]["sources"]],
            [self.document_id],
        )
        self.assertIn("do not repeat", request["payload"]["systemInstruction"]["parts"][0]["text"].casefold())

    def test_adaptive_question_can_decide_to_stop_without_question_or_sources(self):
        from interview_question_service import generate_next_interview_question

        response = gemini_response(json.dumps({
            "should_continue": False,
            "question_text": None,
            "source_ids": [],
        }))
        with patch.object(gemini_client, "generate_content", return_value=response):
            decision = generate_next_interview_question(self.context, self._history())

        self.assertFalse(decision.should_continue)
        self.assertIsNone(decision.question_text)
        self.assertEqual(decision.source_ids, [])

    def test_adaptive_question_rejects_repeated_questions_unknown_sources_and_missing_answers(self):
        from interview_question_service import generate_next_interview_question

        history = self._history()
        repeated = gemini_response(json.dumps({
            "should_continue": True,
            "question_text": history[-1]["question_text"],
            "source_ids": [self.document_id],
        }))
        unknown_source = gemini_response(json.dumps({
            "should_continue": True,
            "question_text": "How is a discrepancy resolved?",
            "source_ids": [self.other_id],
        }))
        for response in (repeated, unknown_source):
            with self.subTest(response=response), patch.object(
                gemini_client, "generate_content", return_value=response
            ):
                with self.assertRaises(InterviewQuestionValidationError):
                    generate_next_interview_question(self.context, history)

        history[-1]["answers"] = []
        with patch.object(gemini_client, "generate_content") as generate:
            with self.assertRaises(InterviewQuestionValidationError):
                generate_next_interview_question(self.context, history)
            generate.assert_not_called()

    def test_adaptive_question_rejects_malformed_decision_and_distinguishes_provider_failure(self):
        from interview_question_service import generate_next_interview_question

        for text in (
            "not JSON",
            json.dumps({"should_continue": True, "question_text": None, "source_ids": []}),
            json.dumps({"should_continue": False, "question_text": "Why?", "source_ids": []}),
            json.dumps({"should_continue": True, "question_text": "Why?", "source_ids": []}),
        ):
            with self.subTest(text=text), patch.object(
                gemini_client,
                "generate_content",
                return_value=gemini_response(text),
            ):
                with self.assertRaises(InterviewQuestionValidationError):
                    generate_next_interview_question(self.context, self._history())

        with patch.object(
            gemini_client,
            "generate_content",
            side_effect=gemini_client.GeminiHTTPError(503, b"private"),
        ):
            with self.assertRaises(InterviewQuestionProviderError):
                generate_next_interview_question(self.context, self._history())


if __name__ == "__main__":
    unittest.main()