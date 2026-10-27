import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from openai import APIStatusError, DefaultHttpxClient, OpenAI

import video_media
import video_vision


class VideoMediaTests(unittest.TestCase):
    def test_probe_duration_streams_resolution_and_corruption(self):
        valid = {
            "format": {"duration": "1200"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
        }
        for duration in ("1200", "0", "1200.01", "nan", "inf", "bad"):
            valid["format"]["duration"] = duration
            with self.subTest(duration=duration), patch("video_media.tool", return_value="ffprobe"), patch(
                "video_media.run_media", return_value=SimpleNamespace(stdout=json.dumps(valid))
            ):
                if duration == "1200":
                    self.assertFalse(video_media.probe_video(Path("x.mp4"))["has_audio"])
                else:
                    with self.assertRaises(HTTPException):
                        video_media.probe_video(Path("x.mp4"))
        for data in ("not json", "{}", '{"format":{"duration":"2"},"streams":[]}'):
            with patch("video_media.tool", return_value="ffprobe"), patch(
                "video_media.run_media", return_value=SimpleNamespace(stdout=data)
            ), self.assertRaises(HTTPException):
                video_media.probe_video(Path("bad.mp4"))
        valid["format"]["duration"] = "1"
        valid["streams"][0]["width"] = 10000
        with patch("video_media.tool", return_value="ffprobe"), patch(
            "video_media.run_media", return_value=SimpleNamespace(stdout=json.dumps(valid))
        ), self.assertRaises(HTTPException):
            video_media.probe_video(Path("oversized.mp4"))

    def test_sampling_covers_start_end_scenes_and_has_bounded_count(self):
        times = video_media.frame_times(1200, [0.2, 45, 90, 130, 175, 225, 270, 310, 350])
        self.assertEqual(times[0], 0)
        self.assertEqual(times[-1], 1199)
        self.assertIn(45, times)
        self.assertLessEqual(len(times), 37)
        self.assertLessEqual(max(b - a for a, b in zip(times, times[1:])), 50)
        self.assertEqual(times, sorted(set(times)))
        self.assertTrue(all(0 <= t < 0.1 for t in video_media.frame_times(0.1, [])))

    def test_tool_failures_sanitized_and_timeouts(self):
        with patch("video_media.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 30)):
            with self.assertRaises(HTTPException) as error:
                video_media.run_media(["ffmpeg"])
            self.assertIn("timed out", error.exception.detail)
        with patch("video_media.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="private paths")):
            with self.assertRaises(HTTPException) as error:
                video_media.run_media(["ffmpeg"])
            self.assertNotIn("private", error.exception.detail)

    def test_vision_configuration_payload_access_and_failure(self):
        client = MagicMock()
        client.models.list.return_value.data = [SimpleNamespace(id="test-vision")]
        with patch.dict(os.environ, {"GROQ_VISION_MODEL": ""}):
            with self.assertRaises(HTTPException):
                video_vision.check_model(client)
        with patch.dict(os.environ, {"GROQ_VISION_MODEL": "unavailable"}):
            with self.assertRaises(HTTPException):
                video_vision.check_model(client)
        with patch.dict(os.environ, {"GROQ_VISION_MODEL": "test-vision"}):
            self.assertEqual(video_vision.check_model(client), "test-vision")
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "frame.jpg"
            frame.write_bytes(b"jpeg-data")
            create = client.chat.completions.with_raw_response.create
            raw = create.return_value
            raw.headers = {}
            raw.parse.return_value.choices = [
                SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Observed diagram")),
            ]
            self.assertEqual(video_vision.describe_frame(client, "test-vision", frame), "Observed diagram")
            payload = create.call_args.kwargs
            self.assertEqual(payload["model"], "test-vision")
            self.assertTrue(payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
            self.assertIn("Do not invent", payload["messages"][0]["content"])
            raw.parse.return_value.choices[0].finish_reason = "length"
            with self.assertRaises(HTTPException):
                video_vision.describe_frame(client, "test-vision", frame)
            failure = APIStatusError(
                "private provider response",
                response=SimpleNamespace(status_code=429, request=None, headers={}), body=None,
            )
            create.side_effect = failure
            with patch.dict(os.environ, {"GROQ_VIDEO_MAX_RETRIES": "0"}), self.assertRaises(HTTPException) as error:
                video_vision.describe_frame(client, "test-vision", frame)
            self.assertEqual(error.exception.status_code, 429)
            self.assertNotIn("private", error.exception.detail)

    def test_real_ffmpeg_silent_and_audio_processing_when_installed(self):
        from dotenv import dotenv_values

        configuration = dotenv_values(Path(__file__).resolve().parent / ".env")
        paths = {
            name: configuration[name] for name in ("FFMPEG_PATH", "FFPROBE_PATH")
            if configuration.get(name) and not os.getenv(name)
        }
        with patch.dict(os.environ, paths):
            self.check_real_ffmpeg_processing()

    def check_real_ffmpeg_processing(self):
        if not all(os.getenv(f"{name.upper()}_PATH") or shutil.which(name) for name in ("ffmpeg", "ffprobe")):
            self.skipTest("FFmpeg/FFprobe not installed; real media smoke test requires these tools")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for audio in (False, True):
                target = root / f"sample-{audio}.mp4"
                command = [video_media.tool("ffmpeg"), "-nostdin", "-loglevel", "error",
                           "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2"]
                if audio:
                    command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"]
                command += ["-c:v", "mpeg4", "-t", "2", str(target)]
                video_media.run_media(command)
                metadata = video_media.probe_video(target)
                self.assertEqual(metadata["has_audio"], audio)
                video_media.find_scenes(target)
                frame = root / f"frame-{audio}.jpg"
                video_media.extract_frame(target, 0.5, frame)
                self.assertGreater(frame.stat().st_size, 0)
                if audio:
                    segments = video_media.extract_audio(target, root)
                    self.assertTrue(segments)
                    self.assertFalse(video_media.is_quiet(segments[0]))

    def test_real_sdk_vision_request_against_local_stub(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps({
                    "id": "local-test", "object": "chat.completion", "created": 0,
                    "model": "test-vision", "choices": [{
                        "index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Observed approval diagram"},
                    }],
                }).encode()
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
        try:
            with tempfile.TemporaryDirectory() as directory, OpenAI(
                api_key="local-test-only",
                base_url=f"http://127.0.0.1:{server.server_port}/openai/v1",
                http_client=DefaultHttpxClient(trust_env=False), max_retries=0,
            ) as client:
                frame = Path(directory) / "frame.jpg"
                frame.write_bytes(b"test-image-bytes")
                self.assertEqual(
                    video_vision.describe_frame(client, "test-vision", frame),
                    "Observed approval diagram",
                )
            self.assertEqual(captured["path"], "/openai/v1/chat/completions")
            self.assertEqual(captured["body"]["max_completion_tokens"], 1200)
            image = captured["body"]["messages"][1]["content"][1]["image_url"]["url"]
            self.assertTrue(image.startswith("data:image/jpeg;base64,"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
