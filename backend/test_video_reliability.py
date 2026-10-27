import os
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from openai import APIConnectionError, APIStatusError

import video_checkpoints
import video_media
from video_requests import VisionRequests, cooldown
from video_visuals import timeline_order
import test_video
import video_routes
import video_processing
from unittest.mock import AsyncMock


def status_error(status, headers=None):
    return APIStatusError(
        "provider body must not escape", response=SimpleNamespace(
            status_code=status, headers=headers or {}, request=None,
        ), body=None,
    )


class VideoRetryTests(unittest.TestCase):
    def setUp(self):
        self.clock = 10000.0
        self.sleeps = []
        patches = [
            patch.dict(os.environ, {
                "GROQ_VIDEO_MAX_RETRIES": "2", "GROQ_VIDEO_RETRY_WAIT_SECONDS": "120",
                "GROQ_VIDEO_REQUEST_INTERVAL_SECONDS": "4",
            }),
            patch("video_requests.time.time", side_effect=lambda: self.clock),
            patch("video_requests.time.monotonic", side_effect=lambda: self.clock),
            patch("video_requests.time.sleep", side_effect=self.sleep),
            patch("video_requests.random.uniform", return_value=.5),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.clock += delay

    def raw(self, headers=None):
        return SimpleNamespace(headers=headers or {}, parse=lambda: "success")

    def test_429_retry_after_and_success_reset_headers_are_respected(self):
        state, frame = {}, {}
        call = MagicMock(side_effect=[
            status_error(429, {"Retry-After": "11"}),
            self.raw({"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "1m2s"}),
            self.raw(),
        ])
        requests = VisionRequests(state)
        self.assertEqual(requests.call(call, frame), "success")
        self.assertIn(11, self.sleeps)
        self.assertEqual(frame["retry_attempts"], 1)
        requests.call(call, {})
        self.assertGreaterEqual(self.sleeps[-1], 62)
        self.assertEqual(state["rate_limits"]["x-ratelimit-reset-tokens"], "1m2s")

    def test_transient_connection_and_503_use_exponential_backoff_with_jitter(self):
        call = MagicMock(side_effect=[
            APIConnectionError(request=None), status_error(503), self.raw(),
        ])
        frame = {}
        self.assertEqual(VisionRequests({}).call(call, frame), "success")
        self.assertEqual(frame["retry_attempts"], 2)
        self.assertEqual(self.sleeps, [0, 4, 4.5])

    def test_budget_exhaustion_is_finite_and_resume_keeps_cooldown(self):
        call = MagicMock(side_effect=status_error(429, {"retry-after": "20"}))
        state, frame = {}, {}
        with self.assertRaises(HTTPException) as error:
            VisionRequests(state).call(call, frame)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(frame["retry_attempts"], 2)
        self.assertIn("exhausted", error.exception.detail)
        self.assertGreater(state["not_before"], self.clock)
        successful = MagicMock(return_value=self.raw())
        VisionRequests(state).call(successful, frame)
        self.assertGreaterEqual(self.sleeps[-1], 20)
        self.assertEqual(frame["retry_attempts"], 3)
        self.assertEqual(frame["request_attempts"], 4)

    def test_retry_after_larger_than_budget_does_not_retry_early(self):
        state = {}
        call = MagicMock(side_effect=status_error(429, {"retry-after": "600"}))
        with self.assertRaises(HTTPException) as error:
            VisionRequests(state).call(call, {})
        self.assertEqual(call.call_count, 1)
        self.assertIn("wait budget", error.exception.detail)
        self.assertEqual(state["not_before"], self.clock + 600)
        resumed_call = MagicMock(return_value=self.raw())
        with self.assertRaises(HTTPException):
            VisionRequests(state).call(resumed_call, {})
        resumed_call.assert_not_called()

    def test_nontransient_error_not_retried(self):
        call = MagicMock(side_effect=status_error(401))
        with self.assertRaises(APIStatusError):
            VisionRequests({}).call(call, {})
        self.assertEqual(call.call_count, 1)

    def test_remaining_token_budget_paces_before_next_frame(self):
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2100))
        raw = SimpleNamespace(
            headers={"x-ratelimit-remaining-tokens": "2500", "x-ratelimit-reset-tokens": "32s"},
            parse=lambda: response,
        )
        requests = VisionRequests({})
        requests.call(MagicMock(return_value=raw), {})
        requests.call(MagicMock(return_value=self.raw()), {})
        self.assertEqual(self.sleeps[-1], 32)

    def test_resumed_controller_preserves_request_interval(self):
        state = {}
        VisionRequests(state).call(MagicMock(return_value=self.raw()), {})
        VisionRequests(state).call(MagicMock(return_value=self.raw()), {})
        self.assertEqual(self.sleeps[-1], 4)

    def test_fractional_retry_count_is_configuration_error(self):
        with patch.dict(os.environ, {"GROQ_VIDEO_MAX_RETRIES": "1.5"}):
            with self.assertRaises(HTTPException) as error:
                VisionRequests({})
        self.assertIn("integer", error.exception.detail)

    def test_header_parsing_dates_milliseconds_and_malformed_values(self):
        date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
        self.assertGreater(cooldown({"retry-after": date}), 28)
        self.assertEqual(cooldown({"retry-after-ms": "2500"}), 2.5)
        self.assertEqual(cooldown({
            "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "1m500ms",
        }), 60.5)
        self.assertEqual(cooldown({"retry-after": "not-a-date"}), 0)
        self.assertEqual(cooldown({"retry-after": "nan"}), 0)


class VideoCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.entries = patch.dict(video_checkpoints._entries, {}, clear=True)
        self.entries.start()
        self.addCleanup(self.entries.stop)

    def test_resume_owner_hash_model_expiry_and_eviction(self):
        key, state = video_checkpoints.checkpoint("admin", "hash", None, "model")
        state["frames"][0] = {"vision_done": True, "vision": "observed UI"}
        self.assertIs(video_checkpoints.checkpoint("admin", "hash", key, "model")[1], state)
        for owner, digest, model, status in (
            ("employee", "hash", "model", 410), ("admin", "different", "model", 409),
            ("admin", "hash", "other-model", 409),
        ):
            with self.assertRaises(HTTPException) as error:
                video_checkpoints.checkpoint(owner, digest, key, model)
            self.assertEqual(error.exception.status_code, status)
        with patch("video_checkpoints.time.time", return_value=time.time() + 3601):
            with self.assertRaises(HTTPException) as error:
                video_checkpoints.checkpoint("admin", "hash", key, "model")
            self.assertEqual(error.exception.status_code, 410)
        for index in range(6):
            video_checkpoints.checkpoint("admin", str(index), None, "model")
        self.assertEqual(len(video_checkpoints._entries), video_checkpoints.MAX_CHECKPOINTS)

    def test_timeline_spreading_and_conservative_duplicate_matching(self):
        order = timeline_order([0, 10, 20, 30, 40, 50])
        self.assertEqual(order[:3], [0, 50, 30])
        self.assertEqual(sorted(order), [0, 10, 20, 30, 40, 50])
        pixels = bytes([100] * (320 * 180))
        self.assertTrue(video_media.same_frame(pixels, pixels))
        changed = bytearray(pixels)
        changed[100:500] = bytes([160] * 400)
        self.assertFalse(video_media.same_frame(pixels, changed))

    def test_scene_changes_are_distributed_over_full_video_not_first_eight(self):
        output = "\n".join(f"pts_time:{index * 10}" for index in range(100))
        with patch("video_media.tool", return_value="ffmpeg"), patch(
            "video_media.run_media", return_value=SimpleNamespace(stderr=output)
        ) as run:
            times = video_media.find_scenes(Path("video.mp4"))
        self.assertEqual(len(times), 12)
        self.assertEqual(times[0], 0)
        self.assertEqual(times[-1], 990)
        self.assertNotIn("-frames:v", run.call_args.args[0])


class VideoResumeTests(unittest.TestCase):
    setUp = test_video.VideoProcessingTests.setUp
    process = test_video.VideoProcessingTests.process

    def test_cancelled_speech_resumes_without_repeating_completed_segments(self):
        self.mocks["probe_video"].return_value["has_audio"] = True
        segments = [self.directory / f"audio-{index:03d}.flac" for index in range(3)]
        for segment in segments:
            segment.write_bytes(b"fLaCdata")
        state = {"frames": {}, "speech": [], "speech_warnings": [], "speech_done": False}
        with patch("video_processing.extract_audio", return_value=segments), patch(
            "video_processing.is_quiet", side_effect=[True, False, False]
        ), patch("video_processing.transcribe_content", side_effect=["First speech", "Remaining speech"]) as speech:
            iterator = video_processing.process_video(self.path, "tutorial.mp4", self.directory, state)
            for event in iterator:
                if event["stage"] == "transcribing" and "3 of 3" in event["message"]:
                    break
            iterator.close()
            self.assertEqual(state["audio_completed"], {0, 1})
            self.assertFalse(state["speech_done"])
            result = list(video_processing.process_video(self.path, "tutorial.mp4", self.directory, state))[-1]
        self.assertEqual([call.args[0] for call in speech.call_args_list], [segments[1].name, segments[2].name])
        self.assertIn("1 audio segment(s) were silent", result["text"])
        self.assertIn("First speech", result["text"])
        self.assertIn("Remaining speech", result["text"])
    def test_partial_resume_reuses_successful_vision_and_complete_speech(self):
        state = {"frames": {}, "speech": [(0, "[00:00:00] Speech transcript\nComplete original speech.")],
                 "speech_done": True, "speech_warnings": []}
        self.mocks["describe_frame"].side_effect = [
            "Observed UI: Google AI Studio. Run button.", HTTPException(429, "retry budget exhausted"),
        ]
        result = list(__import__("video_processing").process_video(
            self.path, "tutorial.mp4", self.directory, state,
        ))[-1]
        self.assertEqual(result["coverage"]["analyzed_timestamps"], [0])
        self.assertEqual(result["coverage"]["skipped"], 2)
        self.assertTrue(result["coverage"]["can_resume"])
        self.assertEqual(result["coverage"]["visual_coverage"], "partial")
        self.assertIn("Complete original speech.", result["text"])
        self.mocks["describe_frame"].reset_mock(side_effect=True)
        self.mocks["describe_frame"].return_value = "Observed UI: System instructions and Run button."
        with patch("video_processing.transcribe_content") as speech:
            resumed = list(__import__("video_processing").process_video(
                self.path, "tutorial.mp4", self.directory, state,
            ))[-1]
        speech.assert_not_called()
        self.assertEqual(self.mocks["describe_frame"].call_count, 2)
        self.assertEqual(resumed["coverage"]["analyzed_timestamps"], [0, 15, 399])
        self.assertFalse(resumed["coverage"]["can_resume"])
        self.assertFalse(resumed["coverage"]["full_video_coverage"])
        self.assertIn("Complete original speech.", resumed["text"])

    def test_duplicate_frames_counted_as_skipped_not_analyzed(self):
        self.mocks["same_frame"].return_value = True
        result = self.process()[-1]
        self.assertEqual(self.mocks["describe_frame"].call_count, 1)
        self.assertEqual(result["coverage"]["selected"], 3)
        self.assertEqual(result["coverage"]["analyzed"], 1)
        self.assertEqual(result["coverage"]["duplicates"], 2)
        self.assertEqual(result["coverage"]["skipped"], 2)
        self.assertFalse(result["coverage"]["can_resume"])
        self.assertIn("Duplicate sampled screen", result["text"])

    def test_uninformative_vision_keeps_speech_and_counts_success(self):
        self.mocks["describe_frame"].return_value = ""
        self.mocks["ocr_frame"].return_value = ""
        state = {"frames": {}, "speech_done": True, "speech_warnings": [],
                 "speech": [(0, "[00:00:00] Speech transcript\nDo not discard this instruction.")]}
        result = list(__import__("video_processing").process_video(
            self.path, "tutorial.mp4", self.directory, state,
        ))[-1]
        self.assertIn("Do not discard this instruction.", result["text"])
        self.assertEqual(result["coverage"]["analyzed"], 3)

    def test_duplicate_uninformative_frames_are_not_savable_knowledge(self):
        self.mocks["same_frame"].return_value = True
        self.mocks["describe_frame"].return_value = ""
        self.mocks["ocr_frame"].return_value = ""
        result = self.process()[-1]
        self.assertFalse(result["has_knowledge"])
        self.assertEqual(result["coverage"]["analyzed"], 1)
        self.assertEqual(result["coverage"]["duplicates"], 2)
        self.assertIn("No useful speech/OCR/visual knowledge", result["text"])

    def test_ocr_labels_deduplicated_against_vision(self):
        self.mocks["describe_frame"].return_value = "Observed UI: Run button and System instructions."
        self.mocks["ocr_frame"].return_value = "Run button\nUnique advanced setting"
        result = self.process()[-1]
        self.assertEqual(result["text"].count("Run button"), 1)
        self.assertEqual(result["text"].count("Unique advanced setting"), 1)
        self.assertEqual(result["text"].count("Repeated extracted instructional findings"), 2)

    def test_ocr_unavailability_does_not_stop_vision(self):
        self.mocks["ocr_frame"].side_effect = HTTPException(503, "Install Tesseract")
        result = self.process()[-1]
        self.assertEqual(result["coverage"]["ocr_status"], "unavailable")
        self.assertEqual(result["coverage"]["analyzed"], 3)
        self.assertIn("Install Tesseract", result["text"])

    def test_comparison_failure_does_not_claim_vision_was_skipped(self):
        self.mocks["frame_pixels"].side_effect = HTTPException(422, "Comparison unavailable")
        result = self.process()[-1]
        self.assertEqual(result["coverage"]["analyzed"], 3)
        self.assertFalse(result["coverage"]["incomplete_reasons"])
        self.assertIn("Duplicate comparison unavailable", result["text"])


class VideoRouteResumeTests(unittest.IsolatedAsyncioTestCase):
    upload = test_video.VideoRouteTests.upload

    async def test_route_checkpoint_resume_binding_and_cleanup(self):
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)
        states, paths = [], []

        def process(path, filename, directory, state):
            states.append(state)
            paths.append(path)
            state["frames"][0] = {"vision_done": True, "vision": "Observed Run button"}
            yield {"stage": "complete", "text": "Observed Run button", "filename": filename}

        with patch.dict(video_checkpoints._entries, {}, clear=True), patch(
            "video_routes.tool", return_value="tool"
        ), patch("video_routes.process_video", side_effect=process):
            response = video_routes.extract_video(request, self.upload(), _user={"sub": "admin"})
            events = [json.loads(event) async for event in response.body_iterator]
            key = events[0]["resume_id"]
            self.assertEqual(events[-1]["resume_id"], key)
            response = video_routes.extract_video(request, self.upload(), resume_id=key, _user={"sub": "admin"})
            resumed = [json.loads(event) async for event in response.body_iterator]
            self.assertEqual(resumed[-1]["resume_id"], key)
            self.assertIs(states[0], states[1])
            self.assertTrue(states[1]["frames"][0]["vision_done"])
            for owner, data, status in (
                ("other-admin", b"\x00\x00\x00\x18ftypisom", 410),
                ("admin", b"\x00\x00\x00\x18ftypisomchanged", 409),
            ):
                upload = self.upload(data=data)
                with self.assertRaises(HTTPException) as error:
                    video_routes.extract_video(request, upload, resume_id=key, _user={"sub": owner})
                self.assertEqual(error.exception.status_code, status)
                self.assertTrue(upload.file.closed)
            self.assertTrue(all(not path.parent.exists() for path in paths))
            self.assertTrue(video_routes.processing_slot.acquire(blocking=False))
            video_routes.processing_slot.release()


if __name__ == "__main__":
    unittest.main()
