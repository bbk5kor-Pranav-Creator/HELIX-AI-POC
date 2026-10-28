import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from contextlib import closing

import knowledge_store


class KnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.database = Path(cls.directory.name) / "knowledge.db"
        cls.original_database = knowledge_store.DATABASE_PATH
        knowledge_store.DATABASE_PATH = cls.database
        # Start with the previous schema to exercise the notes migration.
        with closing(sqlite3.connect(cls.database)) as connection, connection:
            connection.execute(
                """CREATE TABLE knowledge_sources (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    source_type TEXT NOT NULL, original_text TEXT NOT NULL,
                    created_at TEXT NOT NULL)"""
            )
            connection.execute(
                "INSERT INTO knowledge_sources VALUES (?, ?, ?, ?, ?)",
                ("legacy", "Legacy", "paste-text", "Legacy process", "2026-01-01"),
            )
        knowledge_store.initialize_database()
        cls.legacy_notes = knowledge_store.get_source("legacy")["notes"]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "SESSION_SECRET": "test-only-session-secret-with-at-least-32-characters",
            "ADMIN_EMAIL": "admin@example.test",
            "ADMIN_PASSWORD": "test-admin-password",
            "EMPLOYEE_EMAIL": "employee@example.test",
            "EMPLOYEE_PASSWORD": "test-employee-password",
            "HELIX_TEST_DATABASE": str(cls.database),
        })
        code = (
            "import os; from pathlib import Path; import knowledge_store; "
            "knowledge_store.DATABASE_PATH = Path(os.environ['HELIX_TEST_DATABASE']); "
            "import main, uvicorn; "
            f"uvicorn.run(main.app, host='127.0.0.1', port={port}, "
            "access_log=False, log_level='error')"
        )
        cls.server = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parent,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                with urlopen(cls.url, timeout=1):
                    break
            except (URLError, TimeoutError):
                if cls.server.poll() is not None:
                    raise RuntimeError("Test backend exited before startup.")
                time.sleep(0.1)
        else:
            cls.server.terminate()
            cls.server.wait(timeout=10)
            raise RuntimeError("Test backend did not start.")
        cls.admin = cls.login("Admin", "admin", "test-admin-password")
        cls.employee = cls.login("Employee", "employee", "test-employee-password")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        knowledge_store.DATABASE_PATH = cls.original_database
        cls.directory.cleanup()

    @classmethod
    def request(cls, method, path, body=None, token=None):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            cls.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    @classmethod
    def login(cls, role, account, password):
        status, data = cls.request("POST", "/api/auth/login", {
            "email": f"{account}@example.test", "password": password, "role": role,
        })
        if status != 200:
            raise AssertionError(f"{role} test login failed: {status}")
        return data["access_token"]

    def setUp(self):
        with knowledge_store._connection() as connection:
            connection.execute("DELETE FROM knowledge_sources")

    def save(self, title="First source", text="Originalquartz process step.", notes="Context"):
        status, data = self.request(
            "POST", "/api/knowledge/text",
            {"title": title, "text": text, "notes": notes}, self.admin,
        )
        self.assertEqual(status, 200)
        return data["source"]

    def test_migration_retains_existing_source(self):
        self.assertEqual(self.legacy_notes, "")

    def test_restore_update_same_id_and_new_source(self):
        text = "Originalquartz process handoff. " * 150
        source = self.save(text=text, notes="Complete transformation context")
        path = f"/api/knowledge/sources/{source['id']}"
        status, restored = self.request("GET", path, token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(restored["source"]["original_text"], text)
        self.assertEqual(restored["source"]["notes"], "Complete transformation context")
        # Reading and abandoning an edit must not mutate the saved values.
        self.assertEqual(self.request("GET", path, token=self.admin)[1], restored)
        updated_text = "Replacementquartz updated handoff. " * 90
        status, updated = self.request("PUT", path, {
            "title": "Updated title", "text": updated_text, "notes": "Updated notes",
        }, self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(updated["source"]["id"], source["id"])
        self.assertEqual(updated["source"]["original_text"], updated_text)
        self.assertEqual(updated["source"]["notes"], "Updated notes")
        self.assertEqual(knowledge_store.search_chunks("Originalquartz"), [])
        self.assertTrue(knowledge_store.search_chunks("Replacementquartz"))
        other = self.save(title="Second source")
        self.assertNotEqual(other["id"], source["id"])
        self.assertEqual(len(knowledge_store.list_sources()), 2)
        with knowledge_store._connection() as connection:
            chunks = connection.execute(
                "SELECT content FROM knowledge_chunks WHERE source_id = ? ORDER BY chunk_index",
                (source["id"],),
            ).fetchall()
        self.assertEqual([row["content"] for row in chunks], knowledge_store.chunk_text(updated_text))

    def test_delete_cascades_and_preserves_other_sources(self):
        deleted = self.save(text="Deletedquartz process. " * 200)
        other = self.save(title="Preserved", text="Preservedquartz process. " * 150)
        path = f"/api/knowledge/sources/{deleted['id']}"
        status, _ = self.request("DELETE", path, token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", path, token=self.admin)[0], 404)
        status, listed = self.request("GET", "/api/knowledge/sources", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in listed["sources"]], [other["id"]])
        with knowledge_store._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE source_id = ?", (deleted["id"],)
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE source_id = ?", (other["id"],)
            ).fetchone()[0], other["chunk_count"])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.request("POST", "/api/knowledge/interview-context",
            {"query": "Deletedquartz"}, self.employee)[0], 404)
        self.assertEqual(self.request("POST", "/api/knowledge/interview-context",
            {"query": "Preservedquartz"}, self.employee)[0], 200)
        self.assertEqual(self.request("DELETE", path, token=self.admin)[0], 404)

    def test_authorization_and_ids(self):
        source = self.save()
        path = f"/api/knowledge/sources/{source['id']}"
        body = {"title": "Blocked", "text": "Blockedquartz", "notes": ""}
        for method in ("GET", "PUT", "DELETE"):
            request_body = body if method == "PUT" else None
            for token, expected in ((None, 401), ("invalid", 401), (self.employee, 403)):
                self.assertEqual(
                    self.request(method, path, request_body, token)[0], expected
                )
            self.assertEqual(self.request(
                method, "/api/knowledge/sources/not-a-uuid", request_body, self.admin
            )[0], 422)
            self.assertEqual(self.request(
                method, f"/api/knowledge/sources/{uuid4()}", request_body, self.admin
            )[0], 404)
        self.assertIsNotNone(knowledge_store.get_source(source["id"]))

    def test_failed_update_rolls_back_source_and_chunks(self):
        source = self.save()
        before = knowledge_store.get_source(source["id"])
        with knowledge_store._connection() as connection:
            connection.execute(
                """CREATE TRIGGER reject_chunk BEFORE INSERT ON knowledge_chunks
                   BEGIN SELECT RAISE(ABORT, 'test insert failure'); END"""
            )
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                knowledge_store.update_text(source["id"], "Changed", "Different text", "Changed")
            self.assertEqual(knowledge_store.get_source(source["id"]), before)
            self.assertTrue(knowledge_store.search_chunks("Originalquartz"))
        finally:
            with knowledge_store._connection() as connection:
                connection.execute("DROP TRIGGER reject_chunk")

    def test_restart_and_validation(self):
        source = self.save(notes="Restart-persistent notes")
        code = (
            "import sys; from pathlib import Path; import knowledge_store; "
            "knowledge_store.DATABASE_PATH = Path(sys.argv[1]); "
            "source = knowledge_store.get_source(sys.argv[2]); "
            "assert source['notes'] == 'Restart-persistent notes'; "
            "assert source['original_text'] == 'Originalquartz process step.'"
        )
        subprocess.run(
            [sys.executable, "-c", code, str(self.database), source["id"]],
            cwd=Path(__file__).resolve().parent, check=True,
        )
        path = f"/api/knowledge/sources/{source['id']}"
        for body in ({"text": " "}, {"text": "Valid", "notes": "x" * 10001}):
            self.assertEqual(self.request("PUT", path, body, self.admin)[0], 422)
        self.assertEqual(knowledge_store.get_source(source["id"])["title"], source["title"])

    def test_audio_confirmation_edit_retrieve_delete_and_restart(self):
        status, data = self.request("POST", "/api/knowledge/audio", {
            "filename": r"C:\recordings\process.mp3", "title": "Audio process",
            "text": "Audiocobalt handoff. " * 200, "notes": "Reviewed by Admin",
        }, self.admin)
        self.assertEqual(status, 200)
        source = data["source"]
        self.assertEqual(source["source_type"], "Audio")
        self.assertEqual(source["filename"], "process.mp3")
        self.assertGreater(source["chunk_count"], 1)
        path = f"/api/knowledge/sources/{source['id']}"
        self.assertEqual(
            self.request("GET", "/api/knowledge/sources", token=self.admin)[1]["sources"][0]["filename"],
            "process.mp3",
        )
        status, context = self.request("POST", "/api/knowledge/interview-context",
            {"query": "Audiocobalt"}, self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(context["sources"][0]["id"], source["id"])
        status, edited = self.request("PUT", path, {
            "title": "Edited recording", "text": "Editedcobalt handoff.", "notes": "Updated",
        }, self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(edited["source"]["source_type"], "Audio")
        self.assertEqual(edited["source"]["filename"], "process.mp3")
        self.assertEqual(knowledge_store.search_chunks("Audiocobalt"), [])
        code = (
            "import sys; from pathlib import Path; import knowledge_store; "
            "knowledge_store.DATABASE_PATH = Path(sys.argv[1]); "
            "source = knowledge_store.get_source(sys.argv[2]); "
            "assert source['filename'] == 'process.mp3'; "
            "assert source['source_type'] == 'Audio'; "
            "assert knowledge_store.search_chunks('Editedcobalt')"
        )
        subprocess.run(
            [sys.executable, "-c", code, str(self.database), source["id"]],
            cwd=Path(__file__).resolve().parent, check=True,
        )
        self.assertEqual(self.request("DELETE", path, token=self.admin)[0], 200)
        self.assertEqual(self.request("POST", "/api/knowledge/interview-context",
            {"query": "Editedcobalt"}, self.employee)[0], 404)
        self.assertEqual(knowledge_store.search_chunks("Editedcobalt"), [])

    def test_audio_authorization_and_confirm_validation(self):
        body = {"filename": "recording.webm", "text": "Reviewed transcript"}
        for token, expected in ((None, 401), ("invalid", 401), (self.employee, 403)):
            self.assertEqual(self.request(
                "POST", "/api/knowledge/audio", body, token
            )[0], expected)
        for invalid in (
            {**body, "text": " "}, {**body, "filename": "file.txt"},
            {**body, "text": "x" * 200001}, {**body, "notes": "x" * 10001},
        ):
            self.assertIn(self.request(
                "POST", "/api/knowledge/audio", invalid, self.admin
            )[0], (415, 422))
        self.assertEqual(knowledge_store.list_sources(), [])

    def test_audio_multipart_authorization_and_validation(self):
        boundary = "helix-audio-test"
        for token, expected in ((None, 401), ("invalid", 401), (self.employee, 403), (self.admin, 415)):
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="bad.mp3"\r\n'
                "Content-Type: audio/mpeg\r\n\r\n"
                f"not audio\r\n--{boundary}--\r\n"
            ).encode()
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            request = Request(
                self.url + "/api/knowledge/audio/transcribe",
                data=body, headers=headers, method="POST",
            )
            try:
                response = urlopen(request, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                self.assertEqual(response.status, expected)
        self.assertEqual(knowledge_store.list_sources(), [])

    def test_saved_video_source_remains_accessible_without_extraction_backend(self):
        text = "Video: silent tutorial.mp4\nDuration: 00:02:00\n[00:00:30] Observed process steps: Videocobalt intake -> approval. " * 50
        source = knowledge_store.save_text(
            "Reviewed video", text, "Admin checked diagram",
            source_type="Video", filename="silent tutorial.mp4",
        )
        self.assertEqual(source["source_type"], "Video")
        self.assertEqual(source["filename"], "silent tutorial.mp4")
        self.assertGreater(source["chunk_count"], 1)
        path = f"/api/knowledge/sources/{source['id']}"
        status, restored = self.request("GET", path, token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(restored["source"]["original_text"], text)
        listed = self.request("GET", "/api/knowledge/sources", token=self.admin)[1]["sources"]
        self.assertEqual(listed[0]["source_type"], "Video")
        status, context = self.request("POST", "/api/knowledge/interview-context", {
            "query": "process steps responsibilities roles handoffs decisions exceptions controls",
        }, self.employee)
        self.assertEqual(status, 200)
        self.assertIn("Videocobalt", context["chunks"][0]["content"])
        self.assertEqual(context["sources"][0]["id"], source["id"])
        status, edited = self.request("PUT", path, {
            "title": "Edited video", "text": "[00:00:35] Editedvideo process steps.",
            "notes": "Reviewed correction",
        }, self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(edited["source"]["filename"], "silent tutorial.mp4")
        self.assertEqual(edited["source"]["source_type"], "Video")
        self.assertEqual(knowledge_store.search_chunks("Videocobalt"), [])
        code = (
            "import sys; from pathlib import Path; import knowledge_store; "
            "knowledge_store.DATABASE_PATH=Path(sys.argv[1]); "
            "source=knowledge_store.get_source(sys.argv[2]); "
            "assert source['source_type']=='Video'; "
            "assert source['filename']=='silent tutorial.mp4'; "
            "assert '[00:00:35]' in source['original_text']"
        )
        subprocess.run([sys.executable, "-c", code, str(self.database), source["id"]],
                       cwd=Path(__file__).resolve().parent, check=True)
        self.assertEqual(self.request("DELETE", path, token=self.admin)[0], 200)
        self.assertEqual(knowledge_store.search_chunks("Editedvideo"), [])
        with knowledge_store._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE source_id = ?", (source["id"],)
            ).fetchone()[0], 0)

    def test_video_endpoints_are_registered_and_shared_routes_remain(self):
        status, schema = self.request("GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(set(schema["paths"]), {
            "/", "/api/auth/login", "/api/knowledge/text",
            "/api/knowledge/sources", "/api/knowledge/sources/{source_id}",
            "/api/knowledge/interview-context",
            "/api/knowledge/audio/transcribe", "/api/knowledge/audio",
            "/api/knowledge/video/process", "/api/knowledge/video",
            "/api/knowledge/video/jobs/{job_id}",
            "/api/knowledge/video/jobs/{job_id}/result",
        })
        expected_methods = {
            "/": {"get"}, "/api/auth/login": {"post"},
            "/api/knowledge/text": {"post"},
            "/api/knowledge/sources": {"get"},
            "/api/knowledge/sources/{source_id}": {"get", "put", "delete"},
            "/api/knowledge/interview-context": {"post"},
            "/api/knowledge/audio/transcribe": {"post"},
            "/api/knowledge/audio": {"post"},
            "/api/knowledge/video/process": {"post"},
            "/api/knowledge/video": {"post"},
            "/api/knowledge/video/jobs/{job_id}": {"get", "delete"},
            "/api/knowledge/video/jobs/{job_id}/result": {"get"},
        }
        for path, methods in expected_methods.items():
            with self.subTest(path=path):
                self.assertEqual(set(schema["paths"][path]), methods)
        self.assertEqual(self.request("GET", "/"), (
            200, {"message": "Helix AI V2 backend is running"},
        ))
        self.assertEqual(knowledge_store.list_sources(), [])


if __name__ == "__main__":
    unittest.main()
