import io
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from openai import APIConnectionError, APIStatusError, APITimeoutError, DefaultHttpxClient, OpenAI
from starlette.datastructures import Headers

import audio_routes


class AudioTranscriptionTests(unittest.TestCase):
    def setUp(self):
        proxy = patch.dict(os.environ, {"GROQ_PROXY_URL": ""})
        proxy.start()
        self.addCleanup(proxy.stop)

    def upload(self, filename="recording.mp3", data=b"ID3recording", mime="audio/mpeg"):
        return UploadFile(
            filename=filename, file=io.BytesIO(data), size=len(data),
            headers=Headers({"content-type": mime}),
        )

    def test_real_sdk_multipart_request_against_local_stub(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["type"] = self.headers["Content-Type"]
                captured["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"text":"Local SDK transcription"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def local_client(**options):
            self.assertEqual(options["base_url"], "https://api.groq.com/openai/v1")
            options["base_url"] = f"http://127.0.0.1:{server.server_port}/openai/v1"
            return OpenAI(http_client=DefaultHttpxClient(trust_env=False), **options)

        upload = self.upload()
        try:
            with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}), patch(
                "audio_routes.OpenAI", side_effect=local_client
            ):
                self.assertEqual(
                    audio_routes.transcribe_audio(upload)["text"],
                    "Local SDK transcription",
                )
            self.assertEqual(captured["path"], "/openai/v1/audio/transcriptions")
            self.assertIn("multipart/form-data", captured["type"])
            for value in (b"whisper-large-v3-turbo", b"recording.mp3", b"ID3recording"):
                self.assertIn(value, captured["body"])
            self.assertTrue(upload.file.closed)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_supported_formats_preview_only_and_cleanup(self):
        recordings = [
            ("process.mp3", b"ID3recording", "audio/mpeg"),
            ("process.WAV", b"RIFF1234WAVEdata", "audio/wav"),
            ("process.m4a", b"\x00\x00\x00\x18ftypM4A ", "audio/mp4"),
            ("process.webm", b"\x1a\x45\xdf\xa3recording", "audio/webm"),
        ]
        for filename, data, mime in recordings:
            with self.subTest(filename=filename), patch.dict(
                os.environ, {"GROQ_API_KEY": "test-only"}
            ), patch("audio_routes.OpenAI") as sdk, patch(
                "audio_routes.knowledge_store.save_text"
            ) as save:
                sdk.return_value.__enter__.return_value.audio.transcriptions.create.return_value = SimpleNamespace(text="  Reviewed process  ")
                upload = self.upload(filename, data, mime)
                result = audio_routes.transcribe_audio(upload)
                self.assertEqual(result, {"text": "Reviewed process", "filename": filename})
                self.assertTrue(upload.file.closed)
                save.assert_not_called()
                sdk.assert_called_once_with(
                    api_key="test-only", base_url="https://api.groq.com/openai/v1",
                    timeout=120.0, max_retries=0,
                )
                sdk.return_value.__enter__.return_value.audio.transcriptions.create.assert_called_once_with(
                    model="whisper-large-v3-turbo", file=(filename, data),
                    response_format="json",
                )

    def test_invalid_uploads_never_call_provider_and_close_files(self):
        cases = [
            (self.upload("file.txt"), 415),
            (self.upload(data=b""), 422),
            (self.upload(data=b"not audio"), 415),
            (self.upload(mime="text/plain"), 415),
            (self.upload(filename="bad\n.mp3"), 422),
        ]
        oversized = self.upload()
        oversized.size = audio_routes.MAX_AUDIO_BYTES + 1
        cases.append((oversized, 413))
        # The read bound is enforced even when multipart size metadata is absent.
        oversized_stream = self.upload(data=b"ID3" + b"x" * audio_routes.MAX_AUDIO_BYTES)
        oversized_stream.size = None
        cases.append((oversized_stream, 413))
        for upload, status in cases:
            with self.subTest(filename=upload.filename, status=status), patch("audio_routes.OpenAI") as sdk:
                with self.assertRaises(HTTPException) as error:
                    audio_routes.transcribe_audio(upload)
                self.assertEqual(error.exception.status_code, status)
                self.assertTrue(upload.file.closed)
                sdk.assert_not_called()

    def test_exact_size_limit_and_filename_sanitization(self):
        upload = self.upload(
            filename=r"C:\private\process.mp3",
            data=b"ID3" + b"x" * (audio_routes.MAX_AUDIO_BYTES - 3),
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}), patch("audio_routes.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value.audio.transcriptions.create.return_value = SimpleNamespace(text="Process")
            self.assertEqual(audio_routes.transcribe_audio(upload)["filename"], "process.mp3")
        self.assertTrue(upload.file.closed)

    def test_missing_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch("audio_routes.OpenAI") as sdk:
            upload = self.upload()
            with self.assertRaises(HTTPException) as error:
                audio_routes.transcribe_audio(upload)
            self.assertEqual(error.exception.status_code, 503)
            self.assertTrue(upload.file.closed)
            sdk.assert_not_called()

    def test_provider_failures_are_sanitized_and_close_files(self):
        request = SimpleNamespace()
        failures = [
            (APITimeoutError(request=request), 504),
            (APIConnectionError(request=request), 502),
        ]
        for status, expected in ((400, 422), (401, 502), (413, 422), (429, 429), (500, 502)):
            failures.append((
                APIStatusError(
                    "secret provider response",
                    response=SimpleNamespace(status_code=status, request=request, headers={}),
                    body=None,
                ), expected,
            ))
        for failure, status in failures:
            with self.subTest(status=status), patch.dict(
                os.environ, {"GROQ_API_KEY": "test-only"}
            ), patch("audio_routes.OpenAI") as sdk:
                sdk.return_value.__enter__.return_value.audio.transcriptions.create.side_effect = failure
                upload = self.upload()
                with self.assertRaises(HTTPException) as error:
                    audio_routes.transcribe_audio(upload)
                self.assertEqual(error.exception.status_code, status)
                self.assertNotIn("secret", error.exception.detail)
                self.assertTrue(upload.file.closed)

    def test_empty_or_oversized_transcript_rejected(self):
        for text in ("   ", "x" * 200001):
            with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}), patch("audio_routes.OpenAI") as sdk:
                sdk.return_value.__enter__.return_value.audio.transcriptions.create.return_value = SimpleNamespace(text=text)
                upload = self.upload()
                with self.assertRaises(HTTPException) as error:
                    audio_routes.transcribe_audio(upload)
                self.assertEqual(error.exception.status_code, 422)
                self.assertTrue(upload.file.closed)


if __name__ == "__main__":
    unittest.main()
