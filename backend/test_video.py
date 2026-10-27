import io
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

import video_processing
import video_routes
import video_visuals


class VideoProcessingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.directory = Path(directory)
        self.path = self.directory / "upload.mp4"
        self.path.write_bytes(b"video")
        mocks = {
            "probe_video": {"duration": 400.0, "has_audio": False},
            "find_scenes": [15.0],
            "frame_times": [0.0, 15.0, 399.0],
            "extract_frame": None,
            "ocr_frame": "Visible decision label",
            "groq_client": MagicMock(),
            "check_model": "test-vision-model",
            "describe_frame": "Observed: Intake -> Check -> Approved. Inferred: uncertain.",
            "request_interval": 0,
            "frame_pixels": b"one",
            "same_frame": False,
        }
        self.mocks = {
            name: self.stack.enter_context(patch(
                f"{'video_visuals' if name in {'extract_frame', 'frame_pixels', 'same_frame', 'ocr_frame', 'groq_client', 'check_model', 'describe_frame'} else 'video_processing'}.{name}", return_value=value
            )) for name, value in mocks.items()
        }
        self.save = self.stack.enter_context(patch("knowledge_store.save_text"))
        self.stack.enter_context(patch("video_processing.time.sleep"))

    def process(self):
        return list(video_processing.process_video(self.path, "tutorial.mp4", self.directory))

    def test_silent_video_visual_knowledge_and_no_save(self):
        with patch("video_processing.transcribe_content") as speech:
            events = self.process()
        result = events[-1]
        self.assertEqual(result["stage"], "complete")
        self.assertIn("No audio track", result["text"])
        self.assertIn("Local OCR", result["text"])
        self.assertIn("Intake -> Check", result["text"])
        self.assertIn("00:06:39", result["text"])
        self.assertEqual(result["coverage"]["analyzed"], 3)
        self.assertEqual(result["sampled_timestamps"], [0, 15, 399])
        self.assertTrue(any(event["stage"] == "preparing_knowledge" for event in events))
        speech.assert_not_called()
        self.save.assert_not_called()

    def test_audio_segments_order_and_timestamps(self):
        self.mocks["probe_video"].return_value["has_audio"] = True
        segments = [self.directory / f"audio-{index:03d}.flac" for index in range(3)]
        for path in segments:
            path.write_bytes(b"fLaCdata")
        with patch("video_processing.extract_audio", return_value=segments), patch(
            "video_processing.is_quiet", return_value=False
        ), patch(
            "video_processing.transcribe_content",
            side_effect=["First speech", "Second speech", "Third speech"],
        ) as speech:
            result = self.process()[-1]
        self.assertEqual([call.args[0] for call in speech.call_args_list], [p.name for p in segments])
        text = result["text"]
        self.assertIn("[00:00:00 - 00:03:00] Speech", text)
        self.assertIn("[00:03:00 - 00:06:00] Speech", text)
        self.assertIn("[00:06:00 - 00:06:40] Speech", text)
        self.assertLess(text.index("First speech"), text.index("Second speech"))
        self.assertLess(text.index("Second speech"), text.index("Third speech"))

    def test_quiet_audio_is_not_sent_for_transcription(self):
        self.mocks["probe_video"].return_value["has_audio"] = True
        with patch("video_processing.extract_audio", return_value=[Path("quiet.flac")]), patch(
            "video_processing.is_quiet", return_value=True
        ), patch("video_processing.transcribe_content") as speech:
            result = self.process()[-1]
        speech.assert_not_called()
        self.assertIn("below -50 dB", result["text"])
        self.assertIn("Vision interpretation", result["text"])

    def test_audio_extraction_or_provider_failure_preserves_visuals(self):
        self.mocks["probe_video"].return_value["has_audio"] = True
        with patch("video_processing.extract_audio", side_effect=HTTPException(422, "Unsupported audio codec")):
            result = self.process()[-1]
        self.assertIn("Audio extraction failed", result["text"])
        segment = self.directory / "audio-000.flac"
        segment.write_bytes(b"fLaCdata")
        with patch("video_processing.extract_audio", return_value=[segment]), patch(
            "video_processing.is_quiet", return_value=False
        ), patch("video_processing.transcribe_content", side_effect=HTTPException(429, "Transcription service is busy")):
            result = self.process()[-1]
        self.assertIn("remaining speech were not transcribed", result["text"])
        self.assertIn("Vision interpretation", result["text"])

    def test_vision_failure_explicit_partial_ocr_and_no_invented_diagrams(self):
        self.mocks["describe_frame"].side_effect = HTTPException(502, "Vision model has no image access")
        result = self.process()[-1]
        self.assertIn("Vision unfinished", result["text"])
        self.assertIn("Local OCR", result["text"])
        self.assertNotIn("Vision interpretation", result["text"])
        self.assertNotIn("Intake -> Check", result["text"])
        self.assertEqual(self.mocks["describe_frame"].call_count, 1)

    def test_missing_model_and_ocr_returns_actionable_error(self):
        self.mocks["check_model"].side_effect = HTTPException(503, "Set GROQ_VISION_MODEL")
        self.mocks["ocr_frame"].side_effect = HTTPException(503, "Install Tesseract")
        result = self.process()[-1]
        self.assertIn("Set GROQ_VISION_MODEL", result["text"])
        self.assertIn("Install Tesseract", result["text"])
        self.assertFalse(result["has_knowledge"])
        self.assertTrue(result["coverage"]["can_resume"])
        self.save.assert_not_called()

    def test_scene_failure_and_frame_failure_are_reported(self):
        self.mocks["find_scenes"].side_effect = HTTPException(422, "Scene scan failed")
        self.mocks["extract_frame"].side_effect = [HTTPException(422, "Frame decode failed"), None, None]
        result = self.process()[-1]
        self.assertIn("Scene scan failed", result["text"])
        self.assertIn("Frame at 00:00:00 could not be extracted", result["text"])


class VideoRouteTests(unittest.IsolatedAsyncioTestCase):
    def upload(self, data=b"\x00\x00\x00\x18ftypisom", filename="demo.mp4", mime="video/mp4"):
        return UploadFile(
            file=io.BytesIO(data), filename=filename, size=len(data),
            headers=Headers({"content-type": mime}),
        )

    async def test_stream_success_failure_disconnect_cleanup_and_lock(self):
        for outcome in ("success", "failure", "disconnect"):
            upload = self.upload()
            temporary_paths = []

            def process(path, _filename, _directory, _state):
                temporary_paths.append(path)
                yield {"stage": "extracting_audio", "message": "No audio track"}
                if outcome == "failure":
                    raise HTTPException(422, "Corrupted video")
                yield {"stage": "complete", "text": "Observed process"}

            request = MagicMock()
            request.is_disconnected = AsyncMock(return_value=False)
            with self.subTest(outcome=outcome), patch("video_routes.tool", return_value="tool"), patch(
                "video_routes.process_video", side_effect=process
            ):
                response = video_routes.extract_video(request, upload, _user={"sub": "admin-test"})
                self.assertTrue(upload.file.closed)
                if outcome == "disconnect":
                    await anext(response.body_iterator)
                    await anext(response.body_iterator)
                    await response.body_iterator.aclose()
                else:
                    events = [json.loads(event) async for event in response.body_iterator]
                    self.assertEqual(events[-1]["stage"], "error" if outcome == "failure" else "complete")
                self.assertTrue(temporary_paths)
                self.assertFalse(temporary_paths[0].parent.exists())
                self.assertTrue(video_routes.processing_slot.acquire(blocking=False))
                video_routes.processing_slot.release()

    async def test_validation_and_concurrency_always_close_upload(self):
        cases = [
            (self.upload(filename="bad.txt"), 415),
            (self.upload(mime="text/plain"), 415),
            (self.upload(data=b""), 422),
            (self.upload(data=b"not a video"), 415),
        ]
        oversized = self.upload()
        oversized.size = video_routes.MAX_VIDEO_BYTES + 1
        cases.append((oversized, 413))
        for upload, status in cases:
            with self.subTest(status=status), patch("video_routes.tool", return_value="tool"):
                with self.assertRaises(HTTPException) as error:
                    video_routes.extract_video(MagicMock(), upload)
                self.assertEqual(error.exception.status_code, status)
                self.assertTrue(upload.file.closed)
        with patch("video_routes.tool", return_value="tool"), patch(
            "video_routes.MAX_VIDEO_BYTES", 8
        ):
            upload = self.upload()
            upload.size = None
            with self.assertRaises(HTTPException) as error:
                video_routes.extract_video(MagicMock(), upload)
            self.assertEqual(error.exception.status_code, 413)
            self.assertTrue(upload.file.closed)
        video_routes.processing_slot.acquire()
        try:
            upload = self.upload()
            with patch("video_routes.tool", return_value="tool"), self.assertRaises(HTTPException) as error:
                video_routes.extract_video(MagicMock(), upload)
            self.assertEqual(error.exception.status_code, 429)
            self.assertTrue(upload.file.closed)
        finally:
            video_routes.processing_slot.release()

    async def test_missing_ffmpeg_is_actionable_and_closes_upload(self):
        upload = self.upload()
        with patch("video_routes.tool", side_effect=HTTPException(503, "Install ffmpeg")), self.assertRaises(HTTPException) as error:
            video_routes.extract_video(MagicMock(), upload)
        self.assertIn("Install ffmpeg", error.exception.detail)
        self.assertTrue(upload.file.closed)

    async def test_disconnect_before_stream_starts_releases_resources(self):
        paths = []
        original_temporary_directory = tempfile.TemporaryDirectory

        def temporary_directory(**options):
            directory = original_temporary_directory(**options)
            paths.append(Path(directory.name))
            return directory

        with patch("video_routes.tool", return_value="tool"), patch(
            "video_routes.tempfile.TemporaryDirectory", side_effect=temporary_directory
        ):
            response = video_routes.extract_video(MagicMock(), self.upload(), _user={"sub": "admin-test"})
        try:
            with self.assertRaises(ClientDisconnect):
                await response(
                    {"type": "http", "asgi": {"spec_version": "2.4"}},
                    AsyncMock(return_value={"type": "http.disconnect"}),
                    AsyncMock(side_effect=OSError("Client disconnected before headers")),
                )
            self.assertFalse(paths[0].exists())
            self.assertTrue(video_routes.processing_slot.acquire(blocking=False))
            video_routes.processing_slot.release()
        finally:
            # Ensure the failing regression test does not occupy the shared slot.
            if paths[0].exists():
                shutil.rmtree(paths[0])
                video_routes.processing_slot.release()


if __name__ == "__main__":
    unittest.main()
