import tempfile
import unittest
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
            session["id"], {"summary": "First analysis"}
        )
        second = knowledge_store.save_interview_analysis(
            session["id"], {"summary": "Updated analysis"}
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["analysis"], {"summary": "First analysis"})
        self.assertEqual(second["analysis"], {"summary": "Updated analysis"})
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
        knowledge_store.record_interview_answer(question["id"], "Answer.")
        knowledge_store.save_interview_analysis(session["id"], {"summary": "Analysis"})

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
