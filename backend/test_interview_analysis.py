import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import knowledge_store
from interview_analysis import (
    InterviewAnalysis,
    parse_interview_analysis,
    validate_analysis_evidence,
)
from pydantic import ValidationError


class InterviewAnalysisContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "interviews.db"
        database = patch.object(knowledge_store, "DATABASE_PATH", self.database)
        database.start()
        self.addCleanup(database.stop)
        self.addCleanup(self.directory.cleanup)
        knowledge_store.initialize_database()
        self.session = knowledge_store.create_interview_session("employee@example.test")
        source = knowledge_store.save_text(
            "Approval Process", "The manager reviews requests before approval.",
            source_type="Public Link",
        )
        self.question = knowledge_store.add_interview_question(
            self.session["id"], "Who reviews a request?", [source["id"]]
        )
        self.answer = knowledge_store.record_interview_answer(
            self.question["id"], "The manager reviews the request before approval."
        )
        self.session_details = knowledge_store.get_interview_session_details(
            self.session["id"]
        )
        self.source_snapshot = self.question["knowledge_sources"][0]

    def _evidence(self):
        return {
            "question_id": self.question["id"],
            "answer_id": self.answer["id"],
            "answer_excerpt": "The manager reviews the request",
            "supporting_knowledge_sources": [{
                "knowledge_source_id_snapshot": self.source_snapshot[
                    "knowledge_source_id_snapshot"
                ],
                "source_title_snapshot": self.source_snapshot[
                    "source_title_snapshot"
                ],
            }],
        }

    def _payload(self):
        insufficient = {
            "assessment": "insufficient_evidence",
            "rationale": "There is not enough evidence to assess this dimension.",
            "evidence": [],
        }
        assessed = {
            "assessment": "understood",
            "rationale": "The answer accurately describes the documented review step.",
            "evidence": [self._evidence()],
        }
        return {
            "schema_version": "1.0",
            "session_id": self.session["id"],
            "summary": "The response describes the review step.",
            "dimensions": {
                "process_understanding": assessed,
                "role_clarity": {
                    **assessed,
                    "assessment": "partially_understood",
                },
                "workflow_knowledge": {
                    **assessed,
                    "assessment": "not_understood",
                },
                "exception_handling": insufficient,
                "knowledge_gaps": insufficient,
            },
            "strengths": [{
                "statement": "Identified the manager review step.",
                "evidence": [self._evidence()],
                "related_question_ids": [],
            }],
            "improvement_areas": [],
            "identified_gaps": [{
                "statement": "Exception handling was not established.",
                "evidence": [],
                "related_question_ids": [self.question["id"]],
            }],
            "recommended_follow_up_questions": [{
                "question_text": "What happens when a request is an exception?",
                "rationale": "The interview did not establish the exception path.",
                "related_question_ids": [self.question["id"]],
                "evidence": [],
            }],
            "provider_metadata": {
                "provider": "test-provider",
                "model": "test-model",
                "prompt_version": "1",
            },
            "analysis_timestamp": "2026-01-01T00:00:00Z",
        }

    def test_contract_accepts_dimensions_findings_and_typed_evidence(self):
        parsed = validate_analysis_evidence(self._payload(), self.session_details)

        self.assertIsInstance(parsed, InterviewAnalysis)
        self.assertEqual(
            parsed.dimensions.process_understanding.assessment, "understood"
        )
        evidence = parsed.dimensions.process_understanding.evidence[0]
        self.assertEqual(str(evidence.question_id), self.question["id"])
        self.assertEqual(
            str(evidence.supporting_knowledge_sources[0].knowledge_source_id_snapshot),
            self.source_snapshot["knowledge_source_id_snapshot"],
        )
        self.assertEqual(len(parsed.identified_gaps), 1)
        self.assertEqual(len(parsed.recommended_follow_up_questions), 1)

    def test_contract_rejects_missing_dimension_invalid_assessment_and_naive_time(self):
        payload = self._payload()
        del payload["dimensions"]["role_clarity"]
        with self.assertRaises(ValidationError):
            parse_interview_analysis(payload)

        payload = self._payload()
        payload["dimensions"]["role_clarity"]["assessment"] = "unknown"
        with self.assertRaises(ValidationError):
            parse_interview_analysis(payload)

        payload = self._payload()
        payload["analysis_timestamp"] = "2026-01-01T00:00:00"
        with self.assertRaises(ValidationError):
            parse_interview_analysis(payload)

    def test_contract_rejects_assessed_dimension_without_answer_evidence(self):
        payload = self._payload()
        payload["dimensions"]["process_understanding"]["evidence"] = []
        with self.assertRaises(ValidationError):
            parse_interview_analysis(payload)

        payload = self._payload()
        payload["dimensions"]["process_understanding"]["evidence"][0][
            "supporting_knowledge_sources"
        ] = []
        with self.assertRaisesRegex(
            ValidationError, "supporting knowledge sources"
        ):
            parse_interview_analysis(payload)

        payload = self._payload()
        del payload["dimensions"]["process_understanding"]["evidence"]
        with self.assertRaises(ValidationError):
            parse_interview_analysis(payload)

    def test_session_validator_checks_question_answer_and_exact_excerpt(self):
        payload = self._payload()
        payload["dimensions"]["process_understanding"]["evidence"][0][
            "question_id"
        ] = str(uuid4())
        with self.assertRaisesRegex(ValueError, "question does not belong"):
            validate_analysis_evidence(payload, self.session_details)

        payload = self._payload()
        second_question = knowledge_store.add_interview_question(
            self.session["id"], "What happens next?"
        )
        second_answer = knowledge_store.record_interview_answer(
            second_question["id"], "A different response."
        )
        payload["dimensions"]["process_understanding"]["evidence"][0][
            "answer_id"
        ] = second_answer["id"]
        with self.assertRaisesRegex(ValueError, "answer does not belong"):
            validate_analysis_evidence(payload, self.session_details)

        payload = self._payload()
        payload["dimensions"]["process_understanding"]["evidence"][0][
            "answer_excerpt"
        ] = "The manager approves the request"
        with self.assertRaisesRegex(ValueError, "not an exact part"):
            validate_analysis_evidence(payload, self.session_details)

    def test_session_validator_checks_source_and_related_question_ownership(self):
        payload = self._payload()
        source = payload["dimensions"]["process_understanding"]["evidence"][0][
            "supporting_knowledge_sources"
        ][0]
        source["knowledge_source_id_snapshot"] = str(uuid4())
        with self.assertRaisesRegex(ValueError, "source reference does not belong"):
            validate_analysis_evidence(payload, self.session_details)

        payload = self._payload()
        payload["identified_gaps"][0]["related_question_ids"] = [str(uuid4())]
        with self.assertRaisesRegex(ValueError, "Related question does not belong"):
            validate_analysis_evidence(payload, self.session_details)

    def test_payload_size_is_bounded(self):
        payload = self._payload()
        large_evidence = self._evidence()
        large_evidence["answer_excerpt"] = "x" * 2_000
        payload["recommended_follow_up_questions"] = [
            {
                "question_text": "Follow-up?",
                "rationale": "Additional detail is needed.",
                "related_question_ids": [self.question["id"]],
                "evidence": [copy.deepcopy(large_evidence) for _ in range(50)],
            }
            for _ in range(30)
        ]
        with self.assertRaisesRegex(ValueError, "1 MB payload limit"):
            parse_interview_analysis(payload)


if __name__ == "__main__":
    unittest.main()