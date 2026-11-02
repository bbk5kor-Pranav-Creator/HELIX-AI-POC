import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import knowledge_store
from interview_routes import router as interview_router
from knowledge_routes import create_access_token


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
        self.app = FastAPI()
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
        if json is not None:
            options["json"] = json
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
        session = self.create_session().json()["session"]
        source = knowledge_store.save_text(
            "Interview source", "Supervisor approval policy.",
            source_type="Public Link",
        )
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


if __name__ == "__main__":
    unittest.main()
