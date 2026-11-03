import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import knowledge_store


class InterviewStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "knowledge.db"
        self.original_database = knowledge_store.DATABASE_PATH
        knowledge_store.DATABASE_PATH = self.database
        knowledge_store.initialize_database()
        self.addCleanup(self._restore_database)

    def tearDown(self):
        self.directory.cleanup()

    def _restore_database(self):
        knowledge_store.DATABASE_PATH = self.original_database

    def _save_source(self, title="Interview reference"):
        return knowledge_store.save_text(
            title, f"{title} contains interview-relevant guidance.",
            source_type="Public Link",
        )

    @staticmethod
    def _analysis_payload(session, question, answer, summary):
        insufficient = {
            "assessment": "insufficient_evidence",
            "rationale": "No evidence was assessed for this dimension.",
            "evidence": [],
        }
        return {
            "schema_version": "1.0",
            "session_id": session["id"],
            "summary": summary,
            "dimensions": {
                "process_understanding": insufficient,
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

    def test_sessions_are_persistent_per_employee_and_track_status_timestamps(self):
        first = knowledge_store.create_interview_session("employee@example.test")
        second = knowledge_store.create_interview_session("employee@example.test")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "created")
        self.assertEqual(first["employee_id"], "employee@example.test")
        self.assertIsNone(first["started_at"])
        self.assertIsNone(first["finished_at"])

        started = knowledge_store.update_interview_session_status(
            first["id"], "in_progress"
        )
        self.assertEqual(started["status"], "in_progress")
        self.assertIsNotNone(started["started_at"])
        completed = knowledge_store.update_interview_session_status(
            first["id"], "completed"
        )
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["finished_at"])
        self.assertEqual(completed["started_at"], started["started_at"])
        self.assertEqual(
            knowledge_store.get_interview_session(second["id"])["status"], "created"
        )
        failed = knowledge_store.update_interview_session_status(second["id"], "failed")
        self.assertEqual(failed["status"], "failed")
        self.assertIsNotNone(failed["finished_at"])
        with self.assertRaises(knowledge_store.InterviewSessionConflictError):
            knowledge_store.update_interview_session_status(second["id"], "in_progress")
        with self.assertRaises(knowledge_store.InterviewSessionConflictError):
            knowledge_store.add_interview_question(second["id"], "Not allowed.")

    def test_questions_keep_order_and_source_references(self):
        session = knowledge_store.create_interview_session("employee@example.test")
        first_source = self._save_source("First source")
        second_source = self._save_source("Second source")

        first = knowledge_store.add_interview_question(
            session["id"], "What is the first process step?",
            [first_source["id"], second_source["id"], first_source["id"]],
        )
        second = knowledge_store.add_interview_question(
            session["id"], "What follows the first step?"
        )

        self.assertEqual((first["question_order"], second["question_order"]), (0, 1))
        self.assertEqual(
            [item["knowledge_source_id"] for item in first["knowledge_sources"]],
            [first_source["id"], second_source["id"]],
        )
        self.assertEqual(
            [item["source_title_snapshot"] for item in first["knowledge_sources"]],
            ["First source", "Second source"],
        )
        self.assertEqual(second["knowledge_sources"], [])

    def test_interview_category_scope_excludes_other_types_and_pending_docupedia(self):
        document = knowledge_store.save_text(
            "Docs guide", "Documentcobalt process details.", source_type="Document"
        )
        video = knowledge_store.save_text(
            "Video guide", "Videocobalt process details.", source_type="Video"
        )
        pending_docupedia = knowledge_store.save_text(
            "Pending guide", "Pendingcobalt process details.",
            source_type="Docupedia",
            metadata={"docupedia": {"approval_status": "pending"}},
        )

        categories = knowledge_store.list_interview_categories()
        self.assertEqual(
            [(item["id"], item["source_count"]) for item in categories],
            [("docs", 1), ("video", 1)],
        )
        session = knowledge_store.create_interview_session(
            "employee@example.test", "docs"
        )
        self.assertEqual(session["category_id"], "docs")
        self.assertEqual(session["knowledge_source_ids"], [document["id"]])

        context = knowledge_store.get_interview_category_context(
            session["id"], "docs"
        )
        self.assertEqual(
            [source["id"] for source in context["sources"]], [document["id"]]
        )
        self.assertEqual(
            context["sources"][0]["content"],
            "Documentcobalt process details.",
        )
        self.assertTrue(context["chunks"])
        self.assertTrue(all(
            chunk["source_id"] == document["id"] for chunk in context["chunks"]
        ))

        with self.assertRaisesRegex(ValueError, "only sources in the selected category"):
            knowledge_store.add_interview_question(
                session["id"], "Cross-category question?", [video["id"]]
            )
        question = knowledge_store.add_interview_question(
            session["id"], "Docs-only question?", [document["id"]]
        )
        self.assertEqual(
            question["knowledge_sources"][0]["knowledge_source_id_snapshot"],
            document["id"],
        )
        self.assertIsNotNone(knowledge_store.get_source(pending_docupedia["id"]))

    def test_create_interview_session_rejects_empty_or_unknown_categories(self):
        with self.assertRaises(knowledge_store.InterviewCategoryUnavailableError):
            knowledge_store.create_interview_session(
                "employee@example.test", "docs"
            )
        with self.assertRaisesRegex(ValueError, "category is invalid"):
            knowledge_store.get_interview_category_sources("unknown")

    def test_initial_question_insert_is_idempotent_under_concurrent_requests(self):
        source = knowledge_store.save_text(
            "Docs source", "The supervisor reviews each request.",
            source_type="Document",
        )
        session = knowledge_store.create_interview_session(
            "employee@example.test", "docs"
        )

        def insert(question_text):
            return knowledge_store.add_initial_interview_question(
                session["id"], question_text, [source["id"]]
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                insert,
                ("Who reviews each request?", "How does review happen?"),
            ))

        self.assertEqual(results[0][0]["id"], results[1][0]["id"])
        self.assertEqual(sorted(result[1] for result in results), [False, True])
        details = knowledge_store.get_interview_session_details(session["id"])
        self.assertEqual(len(details["questions"]), 1)
        self.assertEqual(
            details["questions"][0]["knowledge_sources"][0][
                "knowledge_source_id_snapshot"
            ],
            source["id"],
        )

    def test_adaptive_successor_requires_answer_and_is_ordered_idempotently(self):
        source = knowledge_store.save_text(
            "Docs source", "The supervisor reviews high-value requests.",
            source_type="Document",
        )
        session = knowledge_store.create_interview_session(
            "employee@example.test", "docs"
        )
        first, _ = knowledge_store.add_initial_interview_question(
            session["id"], "Who reviews high-value requests?", [source["id"]]
        )
        with self.assertRaises(knowledge_store.InterviewSessionConflictError):
            knowledge_store.add_adaptive_followup_question(
                session["id"], first["id"], "How are exceptions handled?", [source["id"]]
            )

        knowledge_store.record_interview_answer(first["id"], "The supervisor reviews them.")
        second, created = knowledge_store.add_adaptive_followup_question(
            session["id"], first["id"], "How are exceptions handled?", [source["id"]]
        )
        repeated, repeated_created = knowledge_store.add_adaptive_followup_question(
            session["id"], first["id"], "A different candidate question?", [source["id"]]
        )

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(second["id"], repeated["id"])
        self.assertEqual(second["question_order"], 1)
        self.assertEqual(
            second["knowledge_sources"][0]["knowledge_source_id_snapshot"],
            source["id"],
        )

    def test_concurrent_adaptive_successor_inserts_create_one_question(self):
        source = knowledge_store.save_text(
            "Docs source", "The supervisor reviews high-value requests.",
            source_type="Document",
        )
        session = knowledge_store.create_interview_session(
            "employee@example.test", "docs"
        )
        first, _ = knowledge_store.add_initial_interview_question(
            session["id"], "Who reviews high-value requests?", [source["id"]]
        )
        knowledge_store.record_interview_answer(first["id"], "The supervisor reviews them.")

        def insert(question_text):
            return knowledge_store.add_adaptive_followup_question(
                session["id"], first["id"], question_text, [source["id"]]
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                insert,
                ("How are exceptions handled?", "What happens for exceptions?"),
            ))

        self.assertEqual(results[0][0]["id"], results[1][0]["id"])
        self.assertEqual(sorted(result[1] for result in results), [False, True])
        details = knowledge_store.get_interview_session_details(session["id"])
        self.assertEqual(len(details["questions"]), 2)
        self.assertEqual(details["questions"][1]["question_order"], 1)

    def test_answers_are_append_only_and_ordered_per_question(self):
        session = knowledge_store.create_interview_session("employee@example.test")
        question = knowledge_store.add_interview_question(
            session["id"], "Describe the approval step."
        )

        first = knowledge_store.record_interview_answer(
            question["id"], "The manager reviews the request."
        )
        second = knowledge_store.record_interview_answer(
            question["id"], "A manager approves requests over the limit."
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((first["answer_order"], second["answer_order"]), (0, 1))
        with knowledge_store._connection() as connection:
            answers = connection.execute(
                """
                SELECT answer_order, answer_text FROM interview_answers
                WHERE question_id = ? ORDER BY answer_order
                """,
                (question["id"],),
            ).fetchall()
        self.assertEqual(
            [(row["answer_order"], row["answer_text"]) for row in answers],
            [
                (0, "The manager reviews the request."),
                (1, "A manager approves requests over the limit."),
            ],
        )

    def test_analysis_is_stored_separately_and_multiple_records_are_preserved(self):
        session = knowledge_store.create_interview_session("employee@example.test")
        question = knowledge_store.add_interview_question(session["id"], "Question?")
        answer = knowledge_store.record_interview_answer(question["id"], "Answer.")

        first = knowledge_store.save_interview_analysis(
            session["id"], self._analysis_payload(
                session, question, answer, "First analysis"
            )
        )
        second = knowledge_store.save_interview_analysis(
            session["id"], self._analysis_payload(
                session, question, answer, "Updated analysis"
            )
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["analysis"]["summary"], "First analysis")
        self.assertEqual(second["analysis"]["summary"], "Updated analysis")
        with knowledge_store._connection() as connection:
            connection.execute(
                "UPDATE interview_analyses SET created_at = ? WHERE session_id = ?",
                ("2026-01-01T00:00:00+00:00", session["id"]),
            )
        history = knowledge_store.get_interview_analyses(session["id"])
        self.assertEqual(
            [record["id"] for record in history],
            sorted((first["id"], second["id"])),
        )
        self.assertEqual(
            [record["analysis"]["summary"] for record in history],
            [record["analysis"]["summary"] for record in sorted(
                (first, second), key=lambda record: record["id"]
            )],
        )
        self.assertEqual(
            knowledge_store.get_interview_question(question["id"])["question_text"],
            "Question?",
        )
        self.assertEqual(
            knowledge_store.get_interview_answer(answer["id"])["answer_text"], "Answer."
        )
        with knowledge_store._connection() as connection:
            analyses = connection.execute(
                "SELECT COUNT(*) FROM interview_analyses WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0]
        self.assertEqual(analyses, 2)

    def test_source_deletion_retains_question_reference_snapshot(self):
        session = knowledge_store.create_interview_session("employee@example.test")
        source = self._save_source()
        question = knowledge_store.add_interview_question(
            session["id"], "Question?", [source["id"]]
        )

        self.assertTrue(knowledge_store.delete_source(source["id"]))
        reference = knowledge_store.get_interview_question(question["id"])[
            "knowledge_sources"
        ][0]
        self.assertIsNone(reference["knowledge_source_id"])
        self.assertEqual(reference["knowledge_source_id_snapshot"], source["id"])
        self.assertEqual(reference["source_title_snapshot"], "Interview reference")

    def test_failed_question_source_insert_rolls_back_question(self):
        session = knowledge_store.create_interview_session("employee@example.test")
        with self.assertRaisesRegex(ValueError, "Knowledge source was not found"):
            knowledge_store.add_interview_question(
                session["id"], "Question?", ["missing-source"]
            )
        with knowledge_store._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM interview_questions WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_foreign_keys_and_session_delete_cascade(self):
        session = knowledge_store.create_interview_session("employee@example.test")
        question = knowledge_store.add_interview_question(session["id"], "Question?")
        answer = knowledge_store.record_interview_answer(question["id"], "Answer.")
        knowledge_store.save_interview_analysis(
            session["id"], self._analysis_payload(
                session, question, answer, "Analysis"
            )
        )

        with knowledge_store._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            connection.execute(
                "DELETE FROM interview_sessions WHERE id = ?", (session["id"],)
            )
            for table, column, value in (
                ("interview_questions", "session_id", session["id"]),
                ("interview_answers", "question_id", question["id"]),
                ("interview_analyses", "session_id", session["id"]),
            ):
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)
                    ).fetchone()[0],
                    0,
                )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_initialization_is_repeatable_and_status_validation_is_explicit(self):
        knowledge_store.initialize_database()
        knowledge_store.initialize_database()
        with self.assertRaisesRegex(ValueError, "status is invalid"):
            knowledge_store.update_interview_session_status("missing", "unknown")
        with self.assertRaisesRegex(ValueError, "Employee identity"):
            knowledge_store.create_interview_session(" ")
        with self.assertRaisesRegex(ValueError, "Interview answer"):
            knowledge_store.record_interview_answer("missing", " ")

        with knowledge_store._connection() as connection:
            session_tables = {
                row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue({
            "interview_sessions", "interview_questions", "interview_question_sources",
            "interview_answers", "interview_analyses",
        }.issubset(session_tables))


if __name__ == "__main__":
    unittest.main()
