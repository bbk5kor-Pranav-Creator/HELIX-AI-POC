import io
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

import knowledge_store
import video_routes
import video_jobs
from knowledge_routes import create_access_token, router as knowledge_router
from video_jobs import VideoJobRegistry


MP4 = b"\x00\x00\x00\x18ftypisom" + b"video"


def extracted(path):
    return {
        "filename": path.name,
        "metadata": {
            "duration_seconds": 10.0, "width": 1280, "height": 720, "has_audio": True,
        },
        "transcript": {
            "language": "en",
            "segments": [{
                "start": 0.0, "end": 2.0,
                "text": "Approval process responsibilities.",
            }],
        },
        "video_sha256": "a" * 64,
        "processing_seconds": 0.1,
        "knowledge_text": "Approval process responsibilities.",
    }


def no_audio(path):
    return {
        "filename": path.name,
        "metadata": {
            "duration_seconds": 10.0, "width": 1280, "height": 720, "has_audio": False,
        },
        "transcript": {"segments": []},
        "processing_seconds": 0.1,
        "knowledge_text": "Video: " + path.name,
    }


def no_usable_speech(path):
    return {
        "filename": path.name,
        "metadata": {
            "duration_seconds": 10.0, "width": 1280, "height": 720, "has_audio": True,
        },
        "transcript": {"segments": []},
        "processing_seconds": 0.1,
        "knowledge_text": "Video: " + path.name,
    }


def probe_output(duration=10, width=1280, height=720, streams=None, container="mov,mp4"):
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({
            "format": {"duration": duration, "format_name": container},
            "streams": streams if streams is not None else [{
                "codec_type": "video", "width": width, "height": height,
            }],
        }),
    )


class VideoExtractErrorMappingTests(unittest.TestCase):
    def test_extract_maps_no_audio_and_no_speech_errors(self):
        class VideoKnowledgeError(ValueError):
            pass

        for message in (
            "No audio track was found. Use a video containing speech.",
            "No usable speech was extracted. Use a video containing clear speech.",
        ):
            with self.subTest(message=message):
                extractor = Mock(side_effect=VideoKnowledgeError(message))
                mocked_video_extractor = ModuleType("video_extractor")
                mocked_video_extractor.VideoKnowledgeError = VideoKnowledgeError
                mocked_video_extractor.extract_video_knowledge = extractor
                video_path = Path("mocked-video.mp4")

                with patch.dict("sys.modules", {"video_extractor": mocked_video_extractor}):
                    with self.assertRaises(video_jobs.VideoJobError) as raised:
                        video_jobs._extract(video_path)

                self.assertEqual(raised.exception.code, "no_knowledge")
                self.assertEqual(str(raised.exception), message)
                extractor.assert_called_once_with(video_path)


class VideoRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "isolated.db"
        database = patch.object(knowledge_store, "DATABASE_PATH", self.database)
        database.start()
        self.addCleanup(database.stop)
        environment = patch.dict(os.environ, {
            "SESSION_SECRET": "video-tests-only-session-secret-longer-than-32",
            "VIDEO_MAX_UPLOAD_MB": "500",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.clock = [time.time()]
        self.extractor = Mock(side_effect=extracted)
        self.registry = VideoJobRegistry(self.extractor, clock=lambda: self.clock[0])
        self.router = video_routes.create_video_router(self.registry)
        self.app = FastAPI()
        self.app.include_router(self.router)
        self.app.include_router(knowledge_router)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        tools = patch("video_routes._tool", return_value="mocked-tool")
        tools.start()
        self.addCleanup(tools.stop)
        probe = patch("video_routes.subprocess.run", return_value=probe_output())
        self.probe = probe.start()
        self.addCleanup(probe.stop)
        self.admin = self.headers("admin@example.test", "Admin")
        self.other = self.headers("other@example.test", "Admin")
        self.employee = self.headers("employee@example.test", "Employee")

    @staticmethod
    def headers(subject, role):
        return {"Authorization": f"Bearer {create_access_token(subject, role)}"}

    def upload(self, filename="demo.mp4", data=MP4, mime="video/mp4", headers=None):
        response = self.client.post(
            "/api/knowledge/video/process",
            headers=self.admin if headers is None else headers,
            files={"file": (filename, data, mime)},
        )
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response

    def completed(self):
        response = self.upload()
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        path = f"/api/knowledge/video/jobs/{job['job_id']}"
        for _ in range(300):
            status = self.client.get(path, headers=self.admin)
            self.assertEqual(status.status_code, 200, status.text)
            if status.json()["state"] in {"completed", "failed"}:
                self.assertEqual(status.json()["state"], "completed", status.text)
                return job, path
            time.sleep(0.005)
        self.fail("Mocked extraction did not complete")

    def test_explicit_confirmation_persistence_retry_and_employee_retrieval(self):
        job, path = self.completed()
        self.assertFalse(self.database.exists(), "Extraction accessed the knowledge store")
        preview = self.client.get(path + "/result", headers=self.admin)
        self.assertEqual(preview.status_code, 200)
        preview_data = preview.json()
        self.assertEqual(preview_data["filename"], "demo.mp4")
        self.assertEqual(preview_data["text"], "Approval process responsibilities.")
        self.assertEqual(preview_data["metadata"], {
            "duration_seconds": 10.0, "width": 1280, "height": 720, "has_audio": True,
        })
        self.assertEqual(preview_data["transcript"], {
            "language": "en",
            "segments": [{
                "start": 0.0, "end": 2.0,
                "text": "Approval process responsibilities.",
            }],
        })
        self.assertEqual(preview.headers["cache-control"], "no-store")
        self.assertFalse(self.database.exists())
        edited_text = "Updated approval workflow for operators."
        body = {"job_id": job["job_id"], "text": edited_text, "notes": "Approved"}
        first = self.client.post("/api/knowledge/video", headers=self.admin, json=body)
        self.assertEqual(first.status_code, 200, first.text)
        source = first.json()["source"]
        self.assertEqual(source["source_type"], "Video")
        self.assertEqual(source["filename"], "demo.mp4")
        self.assertEqual(source["title"], "demo.mp4")
        self.assertEqual(source["metadata"], {
            "video": preview_data["metadata"],
            "transcript": preview_data["transcript"],
            "video_sha256": "a" * 64,
        })
        restored = knowledge_store.get_source(source["id"])
        self.assertEqual(restored["original_text"], edited_text)
        self.assertEqual(restored["metadata"], source["metadata"])
        with knowledge_store._connection() as db:
            chunks = db.execute(
                "SELECT content FROM knowledge_chunks WHERE source_id = ?",
                (source["id"],),
            ).fetchall()
        self.assertEqual([row["content"] for row in chunks], [edited_text])
        self.assertNotIn("00:00", chunks[0]["content"])
        self.assertNotIn("demo.mp4", chunks[0]["content"])
        second = self.client.post("/api/knowledge/video", headers=self.admin, json=body)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(len(knowledge_store.list_sources()), 1)
        self.assertEqual(knowledge_store.get_source(source["id"])["original_text"], body["text"])
        context = self.client.post(
            "/api/knowledge/interview-context", headers=self.employee,
            json={"query": "updated operators"},
        )
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.json()["chunks"][0]["source_id"], source["id"])
        self.assertEqual(context.json()["chunks"][0]["content"], edited_text)
        saved = self.client.get(path, headers=self.admin).json()
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["source_id"], source["id"])
        conflict = self.client.post(
            "/api/knowledge/video", headers=self.admin, json={**body, "text": "Different"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.delete(path, headers=self.admin).status_code, 204)
        self.assertIsNotNone(knowledge_store.get_source(source["id"]))
        self.assertEqual(self.client.get(path, headers=self.admin).status_code, 404)

    def test_speech_only_success_has_no_ocr_fields(self):
        job, path = self.completed()
        result = self.client.get(path + "/result", headers=self.admin).json()
        self.assertNotIn("ocr_results", result)
        self.assertNotIn("frames_analyzed", result)
        self.assertEqual(result["text"], "Approval process responsibilities.")
        self.assertEqual(result["metadata"]["duration_seconds"], 10.0)
        self.assertEqual(result["metadata"]["width"], 1280)
        self.assertEqual(result["metadata"]["height"], 720)
        self.assertEqual(
            result["transcript"]["segments"][0],
            {"start": 0.0, "end": 2.0, "text": "Approval process responsibilities."},
        )
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["has_knowledge"])
        self.assertEqual(self.client.delete(path, headers=self.admin).status_code, 204)

    def test_no_audio_and_no_usable_speech_are_rejected(self):
        for extractor, expected_message in (
            (no_audio, "No audio track was found. Use a video containing speech."),
            (no_usable_speech, "No usable speech was extracted. Use a video containing clear speech."),
        ):
            with self.subTest(extractor=extractor.__name__):
                self.extractor.side_effect = extractor
                response = self.upload()
                self.assertEqual(response.status_code, 202, response.text)
                path = f"/api/knowledge/video/jobs/{response.json()['job_id']}"
                for _ in range(300):
                    status = self.client.get(path, headers=self.admin)
                    if status.json()["state"] in {"completed", "failed"}:
                        break
                    time.sleep(0.005)
                else:
                    self.fail("Mocked extraction did not complete")
                self.assertEqual(status.json()["state"], "failed", status.text)
                self.assertEqual(status.json()["error"]["code"], "no_knowledge")
                self.assertEqual(status.json()["error"]["message"], expected_message)
                self.assertEqual(
                    self.client.get(path + "/result", headers=self.admin).status_code, 409,
                )
                self.assertEqual(self.client.delete(path, headers=self.admin).status_code, 204)

    def test_all_endpoints_require_admin_and_ownership(self):
        job, path = self.completed()
        body = {"job_id": job["job_id"], "text": "Approved"}
        operations = [
            ("GET", path, None), ("GET", path + "/result", None),
            ("POST", "/api/knowledge/video", body), ("DELETE", path, None),
        ]
        for headers, expected in (({}, 401), ({"Authorization": "Bearer invalid"}, 401),
                                  (self.employee, 403), (self.other, 404)):
            for method, url, payload in operations:
                with self.subTest(method=method, headers_role=expected):
                    response = self.client.request(method, url, headers=headers, json=payload)
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
        for headers, expected in (({}, 401), (self.employee, 403),
                                  ({"Authorization": "Bearer invalid"}, 401)):
            self.assertEqual(self.upload(headers=headers).status_code, expected)
        with patch("knowledge_routes.time.time", return_value=self.clock[0] - 40000):
            expired = self.headers("admin@example.test", "Admin")
        self.assertEqual(self.client.get(path, headers=expired).status_code, 401)
        self.assertFalse(self.database.exists())

    def test_filename_mime_header_and_empty_validation(self):
        for filename, data, mime, expected in (
            ("bad.exe", MP4, "video/mp4", 415),
            ("CON.mp4", MP4, "video/mp4", 422),
            ("bad?.mp4", MP4, "video/mp4", 422),
            ("demo.mp4", b"", "video/mp4", 422),
            ("demo.mp4", MP4, "text/plain", 415),
            ("demo.mp4", b"not a container", "video/mp4", 415),
        ):
            with self.subTest(filename=filename, expected=expected):
                self.assertEqual(self.upload(filename, data, mime).status_code, expected)
        self.extractor.assert_not_called()
        # Multipart clients escape newlines; validate the raw-name guard directly.
        with self.assertRaises(video_routes.HTTPException) as error:
            video_routes._filename("bad\n.mp4")
        self.assertEqual(error.exception.status_code, 422)
        response = self.upload(r"C:\recordings\original.mp4")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["filename"], "original.mp4")

    def test_size_limits_actual_size_and_configured_stricter_limit(self):
        with patch.object(video_routes, "MAX_UPLOAD_BYTES", len(MP4) - 1):
            self.assertEqual(self.upload().status_code, 413)
            raw = UploadFile(
                filename="demo.mp4", file=io.BytesIO(MP4), size=None,
                headers=Headers({"content-type": "video/mp4"}),
            )
            path = Path(self.directory.name) / "bound.mp4"
            with self.assertRaises(video_routes.HTTPException) as error:
                video_routes._validate_upload(raw, path, "demo.mp4")
            self.assertEqual(error.exception.status_code, 413)
            raw.file.close()
        with patch.dict(os.environ, {"VIDEO_MAX_UPLOAD_MB": "1"}):
            self.assertEqual(self.upload(data=MP4 + b"x" * (1024 * 1024)).status_code, 413)
        for invalid in ("0", "-1", "invalid"):
            with patch.dict(os.environ, {"VIDEO_MAX_UPLOAD_MB": invalid}):
                self.assertEqual(self.upload().status_code, 503)
        with patch.object(video_routes, "MAX_UPLOAD_BYTES", len(MP4)):
            self.assertEqual(self.upload().status_code, 202)

    def test_ffprobe_metadata_and_errors_are_validated_before_queue(self):
        for output in (
            probe_output(duration=0), probe_output(duration="nan"),
            probe_output(duration=1200.01), probe_output(width=3841),
            probe_output(height=2161), probe_output(width=0),
            probe_output(streams=[{"codec_type": "audio"}]),
            probe_output(container="avi"), SimpleNamespace(returncode=0, stdout="invalid json"),
            SimpleNamespace(returncode=1, stdout="SECRET stderr"),
        ):
            self.probe.return_value = output
            response = self.upload()
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("SECRET", response.text)
        self.extractor.assert_not_called()
        self.probe.side_effect = video_routes.subprocess.TimeoutExpired("private", 30)
        self.assertEqual(self.upload().status_code, 422)
        self.probe.side_effect = OSError("SECRET")
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("SECRET", response.text)
        self.probe.side_effect = None
        self.probe.return_value = probe_output(duration=1200, width=3840, height=2160)
        self.assertEqual(self.upload().status_code, 202)
        arguments = self.probe.call_args.args[0]
        self.assertIn("-protocol_whitelist", arguments)
        self.assertEqual(self.probe.call_args.kwargs["timeout"], 30)

    def test_webm_mov_and_missing_tools(self):
        self.probe.return_value = probe_output(container="matroska,webm")
        response = self.upload("demo.webm", b"\x1a\x45\xdf\xa3webm", "video/webm")
        self.assertEqual(response.status_code, 202)
        self.probe.return_value = probe_output()
        self.assertEqual(
            self.upload("demo.mov", b"\x00\x00\x00\x18moov" + b"data", "video/quicktime").status_code,
            202,
        )
        with patch("video_routes._tool", side_effect=video_routes.HTTPException(
            status_code=503, detail="Local executable unavailable.",
        )):
            self.assertEqual(self.upload().status_code, 503)

    def test_failed_validation_and_registry_rejection_clean_staging_files(self):
        staging = Path(self.directory.name) / "staging"
        staging.mkdir()
        with patch.object(video_routes.tempfile, "tempdir", str(staging)):
            self.probe.return_value = probe_output(duration=0)
            self.assertEqual(self.upload().status_code, 422)
            self.assertEqual(list(staging.iterdir()), [])
            self.probe.return_value = probe_output()
            with patch.object(self.registry, "create", side_effect=video_routes.VideoJobError(
                "capacity", "Video job capacity is full.",
            )):
                response = self.upload()
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers["retry-after"], "10")
            self.assertEqual(list(staging.iterdir()), [])

    def test_processing_discard_result_and_capacity(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def wait(path):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test release timed out")
            return extracted(path)

        self.extractor.side_effect = wait
        first = self.upload().json()
        self.assertTrue(entered.wait(2))
        path = f"/api/knowledge/video/jobs/{first['job_id']}"
        self.assertEqual(self.client.get(path + "/result", headers=self.admin).status_code, 409)
        self.assertEqual(self.client.delete(path, headers=self.admin).status_code, 409)
        self.assertEqual(self.client.post(
            "/api/knowledge/video", headers=self.admin,
            json={"job_id": first["job_id"], "text": "Approved"},
        ).status_code, 409)
        queued = [self.upload().json() for _ in range(3)]
        self.assertEqual(self.upload().status_code, 429)
        self.assertEqual(self.client.delete(
            f"/api/knowledge/video/jobs/{queued[0]['job_id']}", headers=self.admin,
        ).status_code, 204)
        release.set()

    def test_failed_and_expired_jobs(self):
        self.extractor.side_effect = RuntimeError("SECRET media stderr")
        response = self.upload()
        path = f"/api/knowledge/video/jobs/{response.json()['job_id']}"
        for _ in range(200):
            status = self.client.get(path, headers=self.admin)
            if status.json()["state"] == "failed":
                break
            time.sleep(0.005)
        self.assertEqual(status.json()["state"], "failed")
        self.assertNotIn("SECRET", status.text)
        self.assertEqual(self.client.get(path + "/result", headers=self.admin).status_code, 409)
        self.clock[0] += 3601
        self.assertEqual(self.client.get(path, headers=self.admin).status_code, 404)
        self.assertEqual(self.client.get(
            f"/api/knowledge/video/jobs/{uuid4()}", headers=self.admin,
        ).status_code, 404)

    def test_confirmation_validation_and_database_rollback_retry(self):
        job, path = self.completed()
        body = {"job_id": job["job_id"], "text": "Approval process"}
        for change in (
            {"text": " "}, {"text": "x" * 200001}, {"notes": "x" * 10001},
            {"title": "x" * 121}, {"job_id": "invalid"},
        ):
            response = self.client.post(
                "/api/knowledge/video", headers=self.admin, json={**body, **change},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.headers["cache-control"], "no-store")
        knowledge_store.initialize_database()
        with knowledge_store._connection() as db:
            db.execute(
                "CREATE TRIGGER reject_chunk BEFORE INSERT ON knowledge_chunks "
                "BEGIN SELECT RAISE(ABORT, 'SECRET insert failure'); END"
            )
        response = self.client.post("/api/knowledge/video", headers=self.admin, json=body)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("SECRET", response.text)
        self.assertEqual(knowledge_store.list_sources(), [])
        self.assertFalse(self.client.get(path, headers=self.admin).json()["saved"])
        with knowledge_store._connection() as db:
            db.execute("DROP TRIGGER reject_chunk")
        response = self.client.post("/api/knowledge/video", headers=self.admin, json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(knowledge_store.list_sources()), 1)

    def test_concurrent_confirmation_saves_once(self):
        job, _ = self.completed()
        body = {"job_id": job["job_id"], "text": "Approval process"}
        with patch.object(knowledge_store, "save_text", wraps=knowledge_store.save_text) as save:
            with ThreadPoolExecutor(max_workers=4) as executor:
                responses = list(executor.map(
                    lambda _: self.client.post(
                        "/api/knowledge/video", headers=self.admin, json=body,
                    ), range(8),
                ))
            self.assertEqual(save.call_count, 1)
        self.assertEqual({response.status_code for response in responses}, {200})
        self.assertEqual(len({response.json()["source"]["id"] for response in responses}), 1)
        self.assertEqual(len(knowledge_store.list_sources()), 1)

    def test_validation_errors_and_lifespan(self):
        response = self.client.post("/api/knowledge/video/process", headers=self.admin)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get(
            "/api/knowledge/video/jobs/not-a-uuid", headers=self.admin,
        ).status_code, 422)
        registry = VideoJobRegistry(extracted)
        app = FastAPI()
        app.include_router(video_routes.create_video_router(registry))
        with patch.object(registry, "start", wraps=registry.start) as start, patch.object(
            registry, "shutdown", wraps=registry.shutdown,
        ) as shutdown:
            with TestClient(app):
                start.assert_called_once_with()
                shutdown.assert_not_called()
            shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
