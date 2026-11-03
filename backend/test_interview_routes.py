import json
import os
import tempfile
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import knowledge_store
import interview_routes
from interview_analysis_service import (
    InterviewAnalysisProviderError,
    InterviewAnalysisValidationError,
    build_interview_analysis_context,
)
from interview_question_service import InterviewQuestionProviderError
from interview_question_service import (
    GeneratedNextInterviewQuestion,
    InterviewQuestionValidationError,
)
import gemini_client
from interview_routes import router as interview_router
from knowledge_routes import create_access_token, router as knowledge_router


class InterviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "interviews.db"
        database = patch.object(knowledge_store, "DATABASE_PATH", self.database)
        database.start()
        self.addCleanup(database.stop)
        environment = patch.dict(os.environ, {
            "SESSION_SECRET": "interview-tests-session-secret-longer-than-32",
        })
        environment.start()
        self.addCleanup(environment.stop)
        knowledge_store.initialize_database()
        knowledge_store.save_text(
            "Default interview source", "The manager reviews interview requests."
        )
        self.app = FastAPI()
        self.app.include_router(knowledge_router)
        self.app.include_router(interview_router)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(self.directory.cleanup)
        self.employee = self.headers("employee@example.test", "Employee")
        self.other_employee = self.headers("other@example.test", "Employee")
        self.admin = self.headers("admin@example.test", "Admin")

    @staticmethod
    def headers(subject, role):
        token = create_access_token(subject, role)
        return {"Authorization": f"Bearer {token}"}

    def create_session(self, headers=None, json=None):
        options = {"headers": self.employee if headers is None else headers}
        options["json"] = json or {"category_id": "paste-text"}
        return self.client.post("/api/interviews/sessions", **options)

    def question(self, session_id, question_text="Describe the process.", source_ids=None):
        return self.client.post(
            f"/api/interviews/sessions/{session_id}/questions",
            headers=self.employee,
            json={
                "question_text": question_text,
                "knowledge_source_ids": source_ids or [],
            },
        )

    def answer(self, session_id, question_id, text="The manager reviews it."):
        return self.client.post(
            f"/api/interviews/sessions/{session_id}/questions/{question_id}/answers",
            headers=self.employee,
            json={"answer_text": text},
        )

    def completed_session(self):
        session = self.create_session().json()["session"]
        source_id = session["knowledge_source_ids"][0]
        question = self.question(
            session["id"], source_ids=[source_id]
        ).json()["question"]
        answer = self.answer(session["id"], question["id"]).json()["answer"]
        completed = self.client.post(
            f"/api/interviews/sessions/{session['id']}/complete",
            headers=self.employee,
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        return session, question, answer

    @staticmethod
    def analysis_payload(session_id, question, answer, summary="Interview analysis"):
        evidence = {
            "question_id": question["id"],
            "answer_id": answer["id"],
            "answer_excerpt": answer["answer_text"][:200],
            "supporting_knowledge_sources": [{
                "knowledge_source_id_snapshot": source[
                    "knowledge_source_id_snapshot"
                ],
                "source_title_snapshot": source["source_title_snapshot"],
            } for source in question["knowledge_sources"]],
        }
        insufficient = {
            "assessment": "insufficient_evidence",
            "rationale": "The interview did not provide enough evidence.",
            "evidence": [],
        }
        return {
            "schema_version": "1.0",
            "session_id": session_id,
            "summary": summary,
            "dimensions": {
                "process_understanding": {
                    "assessment": "understood",
                    "rationale": "The answer identifies the manager review step.",
                    "evidence": [evidence],
                },
                "role_clarity": insufficient,
                "workflow_knowledge": insufficient,
                "exception_handling": insufficient,
                "knowledge_gaps": insufficient,
            },
            "strengths": [],
            "improvement_areas": [],
            "identified_gaps": [],
            "recommended_follow_up_questions": [],
            "provider_metadata": {
                "provider": "test-provider",
                "model": "test-model",
                "prompt_version": "1",
            },
            "analysis_timestamp": "2026-01-01T00:00:00Z",
        }

    def _save_analysis(self, session_id, question, answer, summary="Interview analysis"):
        return knowledge_store.save_interview_analysis(
            session_id,
            self.analysis_payload(session_id, question, answer, summary),
        )

    @staticmethod
    def question_provider_response(question_text, source_ids):
        return {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps({
                    "question_text": question_text,
                    "source_ids": source_ids,
                })}]},
            }],
        }

    def create_answered_docs_session(self, *, extra_answers=()):
        document = knowledge_store.save_text(
            "Docs source", "The supervisor reviews high-value requests and escalates exceptions.",
            source_type="Document",
        )
        video = knowledge_store.save_text(
            "Video source", "Video-only unrelated information.", source_type="Video"
        )
        session = self.create_session(json={"category_id": "docs"}).json()["session"]
        first, _ = knowledge_store.add_initial_interview_question(
            session["id"], "Who reviews a high-value request?", [document["id"]]
        )
        answers = [knowledge_store.record_interview_answer(
            first["id"], "The supervisor checks the amount."
        )]
        for answer_text in extra_answers:
            answers.append(knowledge_store.record_interview_answer(first["id"], answer_text))
        return session, first, answers, document, video

    def test_employee_authentication_and_server_derived_identity(self):
        self.assertEqual(self.create_session(headers={}).status_code, 401)
        self.assertEqual(self.create_session(headers=self.admin).status_code, 403)
        injected = self.create_session(
            json={"employee_id": "attacker@example.test"}
        )
        self.assertEqual(injected.status_code, 422)

        created = self.create_session()
        self.assertEqual(created.status_code, 201, created.text)
        session = created.json()["session"]
        self.assertEqual(session["employee_id"], "employee@example.test")
        self.assertEqual(session["status"], "created")

    def test_session_list_and_retrieval_are_scoped_to_owner(self):
        own = self.create_session().json()["session"]
        other = self.create_session(headers=self.other_employee).json()["session"]

        listed = self.client.get(
            "/api/interviews/sessions", headers=self.employee
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            [item["id"] for item in listed.json()["sessions"]], [own["id"]]
        )
        self.assertEqual(
            self.client.get(
                f"/api/interviews/sessions/{other['id']}", headers=self.employee
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/api/interviews/sessions/{other['id']}/questions",
                headers=self.employee,
                json={"question_text": "Should not be added."},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/api/interviews/sessions/{other['id']}/complete",
                headers=self.employee,
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/api/interviews/sessions/not-a-uuid", headers=self.employee
            ).status_code,
            422,
        )

    def test_questions_preserve_order_and_source_references(self):
        source = knowledge_store.save_text(
            "Interview source", "Supervisor approval policy.",
            source_type="Public Link",
        )
        session = self.create_session(
            json={"category_id": "public-link"}
        ).json()["session"]
        first = self.question(
            session["id"], "What is the first step?", [source["id"]]
        )
        second = self.question(session["id"], "What is the next step?")

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(first.json()["question"]["question_order"], 0)
        self.assertEqual(second.json()["question"]["question_order"], 1)
        reference = first.json()["question"]["knowledge_sources"][0]
        self.assertEqual(reference["knowledge_source_id"], source["id"])
        self.assertEqual(reference["source_title_snapshot"], "Interview source")

        detail = self.client.get(
            f"/api/interviews/sessions/{session['id']}", headers=self.employee
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(
            [item["question_text"] for item in detail.json()["session"]["questions"]],
            ["What is the first step?", "What is the next step?"],
        )

    def test_answers_are_append_only_and_return_in_session_details(self):
        session = self.create_session().json()["session"]
        question = self.question(session["id"]).json()["question"]
        first = self.answer(session["id"], question["id"], "First answer.")
        second = self.answer(session["id"], question["id"], "Corrected answer.")

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(first.json()["answer"]["answer_order"], 0)
        self.assertEqual(second.json()["answer"]["answer_order"], 1)
        detail = self.client.get(
            f"/api/interviews/sessions/{session['id']}", headers=self.employee
        ).json()["session"]
        self.assertEqual(detail["status"], "in_progress")
        self.assertIsNotNone(detail["started_at"])
        self.assertEqual(
            [answer["answer_text"] for answer in detail["questions"][0]["answers"]],
            ["First answer.", "Corrected answer."],
        )

    def test_validation_question_ownership_and_failed_source_are_handled(self):
        first = self.create_session().json()["session"]
        second = self.create_session(headers=self.other_employee).json()["session"]
        self.assertEqual(
            self.question(first["id"], " ").status_code, 422
        )
        self.assertEqual(
            self.question(first["id"], "Valid?", [str(uuid4())]).status_code, 422
        )
        self.assertEqual(
            knowledge_store.get_interview_session(first["id"])["status"], "created"
        )
        question = self.question(first["id"]).json()["question"]
        other_question = self.client.post(
            f"/api/interviews/sessions/{second['id']}/questions",
            headers=self.other_employee,
            json={"question_text": "Other employee's question."},
        ).json()["question"]
        self.assertEqual(
            self.answer(first["id"], other_question["id"]).status_code, 404
        )
        self.assertEqual(self.answer(first["id"], question["id"], " ").status_code, 422)

    def test_completed_session_is_immutable_and_completion_is_idempotent(self):
        session = self.create_session().json()["session"]
        question = self.question(session["id"]).json()["question"]
        self.assertEqual(
            self.client.post(
                f"/api/interviews/sessions/{session['id']}/complete",
                headers=self.employee,
            ).status_code,
            200,
        )
        repeated = self.client.post(
            f"/api/interviews/sessions/{session['id']}/complete",
            headers=self.employee,
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        completed = repeated.json()["session"]
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["finished_at"])
        self.assertEqual(
            self.question(session["id"], "Another question?").status_code, 409
        )
        self.assertEqual(
            self.answer(session["id"], question["id"], "Late answer.").status_code, 409
        )
        self.assertEqual(
            self.client.get(
                f"/api/interviews/sessions/{session['id']}", headers=self.employee
            ).json()["session"]["questions"][0]["answers"],
            [],
        )

    def test_session_and_answers_persist_across_client_recreation(self):
        session = self.create_session().json()["session"]
        question = self.question(session["id"]).json()["question"]
        self.answer(session["id"], question["id"], "Persistent answer.")

        with TestClient(self.app) as another_client:
            response = another_client.get(
                f"/api/interviews/sessions/{session['id']}", headers=self.employee
            )
        self.assertEqual(response.status_code, 200, response.text)
        persisted = response.json()["session"]
        self.assertEqual(persisted["questions"][0]["answers"][0]["answer_text"],
                         "Persistent answer.")

    def test_analysis_endpoints_require_employee_authentication_and_ownership(self):
        session, _, _ = self.completed_session()
        other_session = self.create_session(headers=self.other_employee).json()["session"]
        generator = patch("interview_routes.generate_interview_analysis")
        generate = generator.start()
        self.addCleanup(generator.stop)
        analysis_path = f"/api/interviews/sessions/{session['id']}/analysis"

        for method in ("post", "get"):
            with self.subTest(method=method):
                request = getattr(self.client, method)
                self.assertEqual(request(analysis_path).status_code, 401)
                self.assertEqual(
                    request(analysis_path, headers=self.admin).status_code, 403
                )
                self.assertEqual(
                    request(
                        f"/api/interviews/sessions/{other_session['id']}/analysis",
                        headers=self.employee,
                    ).status_code,
                    404,
                )
        generate.assert_not_called()

    def test_analysis_post_requires_completed_session(self):
        session = self.create_session().json()["session"]
        generator = patch("interview_routes.generate_interview_analysis")
        generate = generator.start()
        self.addCleanup(generator.stop)

        response = self.client.post(
            f"/api/interviews/sessions/{session['id']}/analysis",
            headers=self.employee,
        )

        self.assertEqual(response.status_code, 409, response.text)
        generate.assert_not_called()

    def test_post_generates_persists_and_get_returns_latest_history(self):
        session, question, answer = self.completed_session()
        call_count = 0

        def generate(session_id):
            nonlocal call_count
            call_count += 1
            return self._save_analysis(
                session_id, question, answer, f"Analysis {call_count}"
            )

        with patch(
            "interview_routes.generate_interview_analysis", side_effect=generate
        ) as generator:
            first = self.client.post(
                f"/api/interviews/sessions/{session['id']}/analysis",
                headers=self.employee,
            )
            second = self.client.post(
                f"/api/interviews/sessions/{session['id']}/analysis",
                headers=self.employee,
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(generator.call_count, 2)
        self.assertEqual(
            first.json()["analysis"]["dimensions"]["process_understanding"][
                "evidence"
            ][0]["answer_id"],
            answer["id"],
        )

        history = self.client.get(
            f"/api/interviews/sessions/{session['id']}/analysis",
            headers=self.employee,
        )
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["latest"]["id"], second.json()["id"])
        self.assertEqual(
            [item["id"] for item in history.json()["history"]],
            [first.json()["id"], second.json()["id"]],
        )
        saved_session = knowledge_store.get_interview_session_details(session["id"])
        self.assertEqual(
            saved_session["questions"][0]["answers"][0]["answer_text"],
            answer["answer_text"],
        )

    def test_analysis_get_returns_explicit_empty_history(self):
        session, _, _ = self.completed_session()

        response = self.client.get(
            f"/api/interviews/sessions/{session['id']}/analysis",
            headers=self.employee,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "session_id": session["id"],
            "latest": None,
            "history": [],
        })

    def test_analysis_failures_are_distinct_sanitized_and_not_saved(self):
        session, _, _ = self.completed_session()
        path = f"/api/interviews/sessions/{session['id']}/analysis"
        failures = (
            (
                InterviewAnalysisProviderError("private provider credential", status_code=429),
                502,
                "Interview analysis provider failed. No analysis was saved.",
            ),
            (
                InterviewAnalysisValidationError("private answer content"),
                422,
                "Generated analysis failed validation. No analysis was saved.",
            ),
        )
        for error, status, detail in failures:
            with self.subTest(error=type(error).__name__), patch(
                "interview_routes.generate_interview_analysis", side_effect=error
            ):
                response = self.client.post(path, headers=self.employee)
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()["detail"], detail)
                self.assertNotIn("private", response.text)
                self.assertEqual(
                    knowledge_store.get_interview_analyses(session["id"]), []
                )

    def test_duplicate_in_flight_posts_share_one_generation_and_record(self):
        session, question, answer = self.completed_session()
        generation_started = Event()
        release_generation = Event()
        waiter_started = Event()
        generator_lock = Lock()
        generator_calls = 0

        class ObservableFuture(Future):
            def result(self, *args, **kwargs):
                waiter_started.set()
                return super().result(*args, **kwargs)

        def generate(session_id):
            nonlocal generator_calls
            with generator_lock:
                generator_calls += 1
            generation_started.set()
            if not release_generation.wait(timeout=5):
                raise TimeoutError("Test did not release the mocked generation.")
            return self._save_analysis(session_id, question, answer)

        path = f"/api/interviews/sessions/{session['id']}/analysis"
        with patch("interview_routes.Future", ObservableFuture), patch(
            "interview_routes.generate_interview_analysis", side_effect=generate
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    self.client.post, path, headers=self.employee
                )
                self.assertTrue(generation_started.wait(timeout=5))
                second = executor.submit(
                    self.client.post, path, headers=self.employee
                )
                self.assertTrue(waiter_started.wait(timeout=5))
                release_generation.set()
                first_response = first.result(timeout=10)
                second_response = second.result(timeout=10)

        self.assertEqual(generator_calls, 1)
        self.assertEqual(first_response.status_code, 200, first_response.text)
        self.assertEqual(second_response.status_code, 200, second_response.text)
        self.assertEqual(first_response.json()["id"], second_response.json()["id"])
        self.assertEqual(len(knowledge_store.get_interview_analyses(session["id"])), 1)

    def test_category_listing_and_context_are_employee_and_scope_restricted(self):
        document = knowledge_store.save_text(
            "Document source", "Onlydocument this process.", source_type="Document"
        )
        video = knowledge_store.save_text(
            "Video source", "Othercategory this process.", source_type="Video"
        )
        pending_docupedia = knowledge_store.save_text(
            "Pending Docupedia", "Pendingcontent process.",
            source_type="Docupedia",
            metadata={"docupedia": {"approval_status": "pending"}},
        )

        categories_path = "/api/interviews/categories"
        self.assertEqual(self.client.get(categories_path).status_code, 401)
        self.assertEqual(
            self.client.get(categories_path, headers=self.admin).status_code, 403
        )
        categories = self.client.get(
            categories_path, headers=self.employee
        ).json()["categories"]
        self.assertEqual(
            [(item["id"], item["source_count"]) for item in categories],
            [("docs", 1), ("video", 1), ("paste-text", 1)],
        )
        self.assertNotIn("docupedia", {item["id"] for item in categories})

        session_response = self.create_session(json={"category_id": "docs"})
        self.assertEqual(session_response.status_code, 201, session_response.text)
        session = session_response.json()["session"]
        self.assertEqual(session["category_id"], "docs")
        self.assertEqual(session["knowledge_source_ids"], [document["id"]])

        context_path = "/api/knowledge/interview-context"
        scoped_request = {
            "session_id": session["id"],
            "category_id": "docs",
        }
        context_response = self.client.post(
            context_path, headers=self.employee, json=scoped_request
        )
        self.assertEqual(context_response.status_code, 200, context_response.text)
        context = context_response.json()
        self.assertEqual(context["category_id"], "docs")
        self.assertEqual(
            [source["id"] for source in context["sources"]], [document["id"]]
        )
        self.assertEqual(
            {chunk["source_id"] for chunk in context["chunks"]}, {document["id"]}
        )
        self.assertEqual(
            context["sources"][0]["content"], "Onlydocument this process."
        )
        self.assertNotIn(video["id"], str(context))
        self.assertNotIn(pending_docupedia["id"], str(context))

        mismatched_category = self.client.post(
            context_path,
            headers=self.employee,
            json={**scoped_request, "category_id": "video"},
        )
        self.assertEqual(mismatched_category.status_code, 409)
        query_bypass = self.client.post(
            context_path,
            headers=self.employee,
            json={"query": "Othercategory"},
        )
        self.assertEqual(query_bypass.status_code, 422)

        foreign_session = self.create_session(
            headers=self.other_employee, json={"category_id": "docs"}
        ).json()["session"]
        foreign_context = self.client.post(
            context_path,
            headers=self.employee,
            json={
                "session_id": foreign_session["id"],
                "category_id": "docs",
            },
        )
        self.assertEqual(foreign_context.status_code, 404)

        cross_category_question = self.question(
            session["id"], "Attempt to cite other category.", [video["id"]]
        )
        self.assertEqual(cross_category_question.status_code, 422)

    def test_session_creation_rejects_unavailable_categories(self):
        response = self.create_session(json={"category_id": "docupedia"})
        self.assertEqual(response.status_code, 409, response.text)

    def test_first_question_uses_only_saved_scope_and_persists_sources(self):
        document = knowledge_store.save_text(
            "Docs source", "The supervisor reviews high-value requests.",
            source_type="Document",
        )
        other = knowledge_store.save_text(
            "Video source", "Video-only unrelated information.",
            source_type="Video",
        )
        session = self.create_session(json={"category_id": "docs"}).json()["session"]
        response_body = self.question_provider_response(
            "How does the supervisor review a high-value request?", [document["id"]]
        )

        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "server-test-key",
            "GEMINI_INTERVIEW_QUESTION_MODEL": "mock-question-model",
        }), patch(
            "interview_question_service.gemini_client.generate_content",
            return_value=response_body,
        ) as generate:
            response = self.client.post(
                f"/api/interviews/sessions/{session['id']}/first-question",
                headers=self.employee,
            )

        self.assertEqual(response.status_code, 200, response.text)
        question = response.json()["question"]
        self.assertEqual(
            question["question_text"],
            "How does the supervisor review a high-value request?",
        )
        self.assertEqual(
            [source["knowledge_source_id_snapshot"] for source in question["knowledge_sources"]],
            [document["id"]],
        )
        self.assertEqual(question["answers"], [])
        provider_call = generate.call_args.kwargs
        self.assertEqual(provider_call["api_key"], "server-test-key")
        self.assertEqual(provider_call["model"], "mock-question-model")
        self.assertNotIn("server-test-key", json.dumps(provider_call["payload"]))
        scoped_prompt = json.loads(
            provider_call["payload"]["contents"][0]["parts"][0]["text"]
        )["interview_context"]
        self.assertEqual(
            [source["id"] for source in scoped_prompt["sources"]], [document["id"]]
        )
        self.assertNotIn(other["id"], json.dumps(provider_call["payload"]))
        persisted = knowledge_store.get_interview_session_details(session["id"])
        self.assertEqual(len(persisted["questions"]), 1)
        self.assertEqual(
            persisted["questions"][0]["knowledge_sources"][0][
                "knowledge_source_id_snapshot"
            ],
            document["id"],
        )

    def test_analysis_uses_exact_source_text_supplied_during_question_generation(self):
        original_text = "The supervisor reviews high-value requests."
        updated_text = "The director approves every request."
        source = knowledge_store.save_text(
            "Docs source", original_text, source_type="Document"
        )
        session = self.create_session(json={"category_id": "docs"}).json()["session"]

        def generate(**request):
            prompt = json.loads(
                request["payload"]["contents"][0]["parts"][0]["text"]
            )
            self.assertEqual(
                prompt["interview_context"]["sources"][0]["content"], original_text
            )
            knowledge_store.update_text(
                source["id"], "Updated Docs source", updated_text, ""
            )
            return self.question_provider_response(
                "How does the supervisor review high-value requests?",
                [source["id"]],
            )

        with patch.dict(os.environ, {"GEMINI_API_KEY": "server-test-key"}), patch(
            "interview_question_service.gemini_client.generate_content",
            side_effect=generate,
        ):
            response = self.client.post(
                f"/api/interviews/sessions/{session['id']}/first-question",
                headers=self.employee,
            )
        self.assertEqual(response.status_code, 200, response.text)
        answer = self.answer(
            session["id"], response.json()["question"]["id"],
            "The supervisor checks the amount.",
        )
        self.assertEqual(answer.status_code, 201, answer.text)
        completed = self.client.post(
            f"/api/interviews/sessions/{session['id']}/complete",
            headers=self.employee,
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        analysis_context = build_interview_analysis_context(session["id"])
        self.assertEqual(analysis_context.sources[0].status, "modified")
        self.assertEqual(analysis_context.sources[0].source_content, original_text)

    def test_first_question_rejects_unauthorized_foreign_and_terminal_sessions(self):
        source = knowledge_store.save_text(
            "Docs source", "Supervisor reviews requests.", source_type="Document"
        )
        own = self.create_session(json={"category_id": "docs"}).json()["session"]
        foreign = self.create_session(
            headers=self.other_employee, json={"category_id": "docs"}
        ).json()["session"]
        path = f"/api/interviews/sessions/{own['id']}/first-question"
        foreign_path = f"/api/interviews/sessions/{foreign['id']}/first-question"
        for headers, expected in ((None, 401), (self.admin, 403)):
            response = self.client.post(path, headers=headers)
            self.assertEqual(response.status_code, expected)
        self.assertEqual(
            self.client.post(foreign_path, headers=self.employee).status_code, 404
        )

        for status in ("completed", "failed"):
            session = self.create_session(json={"category_id": "docs"}).json()["session"]
            knowledge_store.update_interview_session_status(session["id"], status)
            with patch("interview_routes.generate_first_interview_question") as generate:
                response = self.client.post(
                    f"/api/interviews/sessions/{session['id']}/first-question",
                    headers=self.employee,
                )
            self.assertEqual(response.status_code, 409, response.text)
            generate.assert_not_called()
        self.assertIsNotNone(source)

    def test_first_question_rejects_invalid_model_output_and_sanitizes_provider_errors(self):
        source = knowledge_store.save_text(
            "Docs source", "Supervisor reviews requests.", source_type="Document"
        )
        session = self.create_session(json={"category_id": "docs"}).json()["session"]
        path = f"/api/interviews/sessions/{session['id']}/first-question"
        invalid_results = (
            self.question_provider_response("Who reviews it?", [str(uuid4())]),
            {
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "not JSON"}]},
                }],
            },
        )
        for result in invalid_results:
            with self.subTest(result=result), patch.dict(
                os.environ, {"GEMINI_API_KEY": "server-test-key"}
            ), patch(
                "interview_question_service.gemini_client.generate_content",
                return_value=result,
            ):
                response = self.client.post(path, headers=self.employee)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(
                    knowledge_store.get_interview_session_details(session["id"])[
                        "questions"
                    ],
                    [],
                )

        with patch.dict(os.environ, {"GEMINI_API_KEY": "server-test-key"}), patch(
            "interview_question_service.gemini_client.generate_content",
            side_effect=gemini_client.GeminiHTTPError(429, b"secret provider body"),
        ):
            response = self.client.post(path, headers=self.employee)
        self.assertEqual(response.status_code, 429)
        self.assertIn("quota or rate limit", response.json()["detail"])
        self.assertNotIn("secret", response.text)
        self.assertEqual(
            knowledge_store.get_interview_session_details(session["id"])["questions"],
            [],
        )
        self.assertIsNotNone(source)

    def test_first_question_provider_failures_have_safe_actionable_categories(self):
        knowledge_store.save_text(
            "Docs source", "Supervisor reviews requests.", source_type="Document"
        )
        session = self.create_session(json={"category_id": "docs"}).json()["session"]
        path = f"/api/interviews/sessions/{session['id']}/first-question"
        failures = (
            (gemini_client.GeminiHTTPError(401, b"secret key detail"), 503, "GEMINI_API_KEY"),
            (gemini_client.GeminiHTTPError(403, b"secret permission detail"), 503, "project access"),
            (gemini_client.GeminiHTTPError(404, b"secret model detail"), 503, "model is unavailable"),
            (gemini_client.GeminiHTTPError(429, b"secret quota detail"), 429, "quota or rate limit"),
            (gemini_client.GeminiTransportError("secret network detail"), 502, "connectivity"),
        )
        for error, expected_status, expected_message in failures:
            with self.subTest(error=type(error).__name__, status=expected_status), patch.dict(
                os.environ, {"GEMINI_API_KEY": "server-test-key"}
            ), patch(
                "interview_question_service.gemini_client.generate_content",
                side_effect=error,
            ):
                response = self.client.post(path, headers=self.employee)
            self.assertEqual(response.status_code, expected_status, response.text)
            self.assertIn(expected_message, response.json()["detail"])
            self.assertNotIn("secret", response.text)
        self.assertEqual(
            knowledge_store.get_interview_session_details(session["id"])["questions"],
            [],
        )

    def test_first_question_repeated_requests_return_existing_without_regeneration(self):
        source = knowledge_store.save_text(
            "Docs source", "Supervisor reviews requests.", source_type="Document"
        )
        session = self.create_session(json={"category_id": "docs"}).json()["session"]
        result = self.question_provider_response("How are requests reviewed?", [source["id"]])
        with patch.dict(os.environ, {"GEMINI_API_KEY": "server-test-key"}), patch(
            "interview_question_service.gemini_client.generate_content",
            return_value=result,
        ) as generate:
            path = f"/api/interviews/sessions/{session['id']}/first-question"
            first = self.client.post(path, headers=self.employee)
            repeated = self.client.post(path, headers=self.employee)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(first.json(), repeated.json())
        generate.assert_called_once()
        self.assertEqual(
            len(knowledge_store.get_interview_session_details(session["id"])["questions"]),
            1,
        )

    def test_concurrent_first_question_requests_persist_exactly_one_question(self):
        source = knowledge_store.save_text(
            "Docs source", "Supervisor reviews requests.", source_type="Document"
        )
        session = self.create_session(json={"category_id": "docs"}).json()["session"]
        provider_barrier = Barrier(2)

        def generate(**_kwargs):
            provider_barrier.wait(timeout=5)
            return self.question_provider_response(
                "How are requests reviewed?", [source["id"]]
            )

        path = f"/api/interviews/sessions/{session['id']}/first-question"
        with patch.dict(os.environ, {"GEMINI_API_KEY": "server-test-key"}), patch(
            "interview_question_service.gemini_client.generate_content",
            side_effect=generate,
        ) as provider:
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(
                    lambda _index: self.client.post(path, headers=self.employee),
                    range(2),
                ))

        self.assertEqual(provider.call_count, 2)
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(responses[0].json(), responses[1].json())
        questions = knowledge_store.get_interview_session_details(session["id"])[
            "questions"
        ]
        self.assertEqual(len(questions), 1)

    def test_next_question_uses_ordered_database_history_and_scoped_context(self):
        session, first, answers, document, video = self.create_answered_docs_session(
            extra_answers=("The requester provides missing details.",)
        )
        decision = GeneratedNextInterviewQuestion.model_validate({
            "should_continue": True,
            "question_text": "What happens when the request exceeds the supervisor's limit?",
            "source_ids": [document["id"]],
        })
        with patch(
            "interview_routes.generate_next_interview_question",
            return_value=decision,
        ) as generate:
            response = self.client.post(
                f"/api/interviews/sessions/{session['id']}/next-question",
                headers=self.employee,
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["should_continue"])
        self.assertEqual(body["question"]["question_order"], 1)
        self.assertEqual(body["question"]["question_text"], decision.question_text)
        self.assertEqual(
            [reference["knowledge_source_id_snapshot"]
             for reference in body["question"]["knowledge_sources"]],
            [document["id"]],
        )
        scoped_context, history = generate.call_args.args
        self.assertEqual(scoped_context["category_id"], "docs")
        self.assertEqual([source["id"] for source in scoped_context["sources"]], [document["id"]])
        self.assertNotIn(video["id"], json.dumps(scoped_context))
        self.assertEqual([turn["question_order"] for turn in history], [0])
        self.assertEqual(
            [answer["answer_text"] for answer in history[0]["answers"]],
            [answer["answer_text"] for answer in answers],
        )
        self.assertEqual(history[0]["knowledge_sources"][0][
            "knowledge_source_id_snapshot"
        ], document["id"])

    def test_next_question_requires_latest_answer_and_rejects_foreign_or_terminal_sessions(self):
        document = knowledge_store.save_text(
            "Docs source", "The supervisor reviews high-value requests.",
            source_type="Document",
        )
        unanswered = self.create_session(json={"category_id": "docs"}).json()["session"]
        knowledge_store.add_initial_interview_question(
            unanswered["id"], "Who reviews the request?", [document["id"]]
        )
        other = self.create_session(
            headers=self.other_employee, json={"category_id": "docs"}
        ).json()["session"]
        path = f"/api/interviews/sessions/{unanswered['id']}/next-question"
        with patch("interview_routes.generate_next_interview_question") as generate:
            self.assertEqual(self.client.post(path).status_code, 401)
            self.assertEqual(
                self.client.post(path, headers=self.admin).status_code, 403
            )
            self.assertEqual(
                self.client.post(
                    f"/api/interviews/sessions/{other['id']}/next-question",
                    headers=self.employee,
                ).status_code,
                404,
            )
            missing_answer = self.client.post(path, headers=self.employee)
            self.assertEqual(missing_answer.status_code, 409, missing_answer.text)
            generate.assert_not_called()

        for status in ("completed", "failed"):
            terminal = self.create_session(json={"category_id": "docs"}).json()["session"]
            knowledge_store.update_interview_session_status(terminal["id"], status)
            with patch("interview_routes.generate_next_interview_question") as generate:
                response = self.client.post(
                    f"/api/interviews/sessions/{terminal['id']}/next-question",
                    headers=self.employee,
                )
            self.assertEqual(response.status_code, 409, response.text)
            generate.assert_not_called()

    def test_next_question_stop_decision_does_not_complete_session(self):
        session, first_question, answers, _, _ = self.create_answered_docs_session()
        stop = GeneratedNextInterviewQuestion.model_validate({
            "should_continue": False,
            "question_text": None,
            "source_ids": [],
        })
        with patch(
            "interview_routes.generate_next_interview_question", return_value=stop
        ) as generate:
            response = self.client.post(
                f"/api/interviews/sessions/{session['id']}/next-question",
                headers=self.employee,
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"should_continue": False, "question": None})
        generate.assert_called_once()
        self.assertEqual(
            knowledge_store.get_interview_session(session["id"])["status"],
            "in_progress",
        )
        self.assertEqual(
            len(knowledge_store.get_interview_session_details(session["id"])["questions"]),
            1,
        )
        completed = self.client.post(
            f"/api/interviews/sessions/{session['id']}/complete",
            headers=self.employee,
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        with patch(
            "interview_routes.generate_interview_analysis",
            side_effect=lambda session_id: self._save_analysis(
                session_id, first_question, answers[0]
            ),
        ):
            analysis_response = self.client.post(
                f"/api/interviews/sessions/{session['id']}/analysis",
                headers=self.employee,
            )
        self.assertEqual(analysis_response.status_code, 200, analysis_response.text)

    def test_next_question_provider_and_validation_errors_are_not_persisted(self):
        session, _, _, _, _ = self.create_answered_docs_session()
        path = f"/api/interviews/sessions/{session['id']}/next-question"
        cases = (
            (
                InterviewQuestionProviderError("private provider details"),
                502,
                "Interview follow-up provider failed. No question was saved.",
            ),
            (
                InterviewQuestionValidationError("private answer data"),
                422,
                "Generated follow-up failed validation. No question was saved.",
            ),
        )
        for error, status, detail in cases:
            with self.subTest(error=type(error).__name__), patch(
                "interview_routes.generate_next_interview_question", side_effect=error
            ):
                response = self.client.post(path, headers=self.employee)
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()["detail"], detail)
                self.assertNotIn("private", response.text)
                self.assertEqual(
                    len(knowledge_store.get_interview_session_details(session["id"])["questions"]),
                    1,
                )

    def test_next_question_repeated_and_concurrent_requests_return_one_successor(self):
        session, first, _, document, _ = self.create_answered_docs_session()
        decision = GeneratedNextInterviewQuestion.model_validate({
            "should_continue": True,
            "question_text": "How is an exception escalated?",
            "source_ids": [document["id"]],
        })
        path = f"/api/interviews/sessions/{session['id']}/next-question"
        with patch(
            "interview_routes.generate_next_interview_question", return_value=decision
        ) as generate:
            first_response = self.client.post(path, headers=self.employee)
            repeated_response = self.client.post(path, headers=self.employee)
        self.assertEqual(first_response.status_code, 200, first_response.text)
        self.assertEqual(repeated_response.json(), first_response.json())
        generate.assert_called_once()

        second_session, second_question, _, second_source, _ = self.create_answered_docs_session()
        barrier = Barrier(2)

        def generate_concurrently(*_args):
            barrier.wait(timeout=5)
            return GeneratedNextInterviewQuestion.model_validate({
                "should_continue": True,
                "question_text": "What is the escalation path?",
                "source_ids": [second_source["id"]],
            })

        second_path = f"/api/interviews/sessions/{second_session['id']}/next-question"
        with patch(
            "interview_routes.generate_next_interview_question",
            side_effect=generate_concurrently,
        ) as concurrent_generate:
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(
                    lambda _index: self.client.post(
                        second_path, headers=self.employee
                    ),
                    range(2),
                ))

        self.assertEqual(concurrent_generate.call_count, 2)
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(responses[0].json(), responses[1].json())
        stored = knowledge_store.get_interview_session_details(second_session["id"])
        self.assertEqual(len(stored["questions"]), 2)
        self.assertEqual(stored["questions"][1]["question_order"], 1)
        self.assertEqual(stored["questions"][1]["knowledge_sources"][0][
            "knowledge_source_id_snapshot"
        ], second_source["id"])
        self.assertEqual(stored["questions"][0]["id"], second_question["id"])


if __name__ == "__main__":
    unittest.main()
