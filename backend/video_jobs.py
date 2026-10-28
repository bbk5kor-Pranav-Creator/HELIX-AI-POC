"""Local video jobs; no SQLite writes or HTTP dependencies.

Use one registry per backend process and explicitly call start()/shutdown().
Jobs and previews are lost on restart; multiple Uvicorn workers are unsupported.
Shutdown waits for active extraction and uploads: unchanged extraction cannot be
forcibly cancelled. Forced process termination cannot guarantee file cleanup.
"""

import logging
import math
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal
from uuid import uuid4


logger = logging.getLogger(__name__)
MAX_JOBS = 4
MAX_UPLOAD_BYTES = 500_000_000
MAX_TEXT_CHARACTERS = 200_000
RETENTION_SECONDS = 3600
JobState = Literal["queued", "processing", "completed", "failed"]
Extractor = Callable[[Path], Mapping[str, object]]


class VideoJobError(Exception):
    """A stable code and safe message suitable for an eventual API adapter."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class _Job:
    job_id: str
    owner: str
    filename: str
    directory: Path
    created_at: float
    state: JobState = "queued"
    expires_at: float | None = None
    preview: dict[str, object] | None = None
    error: dict[str, str] | None = None


def _extract(path: Path) -> Mapping[str, object]:
    # Keep heavy imports/model initialization out of application startup.
    from video_extractor import VideoKnowledgeError, extract_video_knowledge

    try:
        return extract_video_knowledge(path)
    except VideoKnowledgeError as error:
        raise VideoJobError("no_knowledge", str(error)) from error


def _safe_filename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    stem = name.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    if (
        not name or len(name) > 255 or name.endswith((" ", "."))
        or any(ord(char) < 32 or char in '<>:"|?*' for char in name)
        or stem in reserved
    ):
        raise VideoJobError("invalid_filename", "Invalid video filename.")
    if PurePosixPath(name).suffix.lower() not in {".mp4", ".mov", ".webm"}:
        raise VideoJobError("unsupported_video", "Use MP4, MOV, or WebM video.")
    return name


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _preview(result: Mapping[str, object], filename: str) -> dict[str, object]:
    transcript = result.get("transcript")
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    if not isinstance(segments, list):
        raise VideoJobError("invalid_result", "Video extraction returned an invalid result.")
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise VideoJobError("invalid_result", "Video extraction returned invalid metadata.")
    duration = metadata.get("duration_seconds")
    width, height = metadata.get("width"), metadata.get("height")
    if (
        not isinstance(duration, (int, float)) or isinstance(duration, bool)
        or not math.isfinite(duration) or duration <= 0
        or not isinstance(width, int) or isinstance(width, bool) or width <= 0
        or not isinstance(height, int) or isinstance(height, bool) or height <= 0
        or type(metadata.get("has_audio")) is not bool
    ):
        raise VideoJobError("invalid_result", "Video extraction returned invalid metadata.")
    if not metadata["has_audio"]:
        raise VideoJobError(
            "no_knowledge", "No audio track was found. Use a video containing speech.",
        )
    spoken_segments = [
        item for item in segments
        if isinstance(item, dict) and isinstance(item.get("text"), str)
        and item["text"].strip()
    ]
    if not spoken_segments:
        raise VideoJobError(
            "no_knowledge",
            "No usable speech was extracted. Use a video containing clear speech.",
        )
    text = " ".join(item["text"].strip() for item in spoken_segments)
    if len(text) > MAX_TEXT_CHARACTERS:
        raise VideoJobError(
            "result_too_large",
            "Extracted knowledge exceeds 200,000 characters. Use a shorter video.",
        )
    elapsed = result.get("processing_seconds")
    if (
        not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
        or not math.isfinite(elapsed) or elapsed < 0
    ):
        raise VideoJobError("invalid_result", "Video extraction returned invalid statistics.")
    return {
        "filename": filename, "text": text,
        "metadata": {
            "duration_seconds": duration, "width": width, "height": height,
            "has_audio": metadata["has_audio"],
        },
        "transcript": {
            "language": transcript.get("language"),
            "segments": segments,
        },
        "video_sha256": result.get("video_sha256"),
        "processing_seconds": elapsed,
        "has_knowledge": True, "warnings": [],
    }


class VideoJobRegistry:
    """Thread-safe four-slot registry with one worker and terminal-result expiry.

    create() copies a caller-owned binary stream; it never closes that stream.
    HTTP adapters must still validate MIME/container/metadata before extraction.
    Returned snapshots are detached copies. Polling does not renew retention.
    """

    def __init__(
        self, extractor: Extractor = _extract, *,
        clock: Callable[[], float] = time.time,
    ):
        self._extractor = extractor
        self._clock = clock
        self._condition = threading.Condition()
        self._jobs: dict[str, _Job] = {}
        self._reservations = 0
        self._pending_cleanup: set[Path] = set()
        self._thread: threading.Thread | None = None
        self._accepting = False

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                if self._accepting:
                    return
                raise VideoJobError("registry_stopped", "Video jobs cannot restart in this registry.")
            self._accepting = True
            thread = threading.Thread(target=self._run, name="helix-video-jobs")
            try:
                thread.start()
            except RuntimeError:
                self._accepting = False
                logger.exception("Unable to start video worker")
                raise VideoJobError("unavailable", "Video processing is unavailable.") from None
            self._thread = thread

    def create(self, owner: str, filename: str, upload: BinaryIO) -> dict[str, object]:
        filename = _safe_filename(filename)
        if not owner.strip():
            raise VideoJobError("invalid_owner", "A job owner is required.")
        with self._condition:
            self._cleanup_locked()
            if not self._accepting:
                raise VideoJobError("unavailable", "Video processing is not accepting uploads.")
            if len(self._jobs) + self._reservations >= MAX_JOBS:
                raise VideoJobError("capacity", "Video job capacity is full. Try again later.")
            self._reservations += 1
        directory = None
        accepted = False
        try:
            directory = Path(tempfile.mkdtemp(prefix="helix-video-job-"))
            size = 0
            with (directory / filename).open("wb") as target:
                while block := upload.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_UPLOAD_BYTES:
                        raise VideoJobError("upload_too_large", "Video must be 500 MB or smaller.")
                    target.write(block)
            if not size:
                raise VideoJobError("empty_upload", "Video file is empty.")
            with self._condition:
                if not self._accepting:
                    raise VideoJobError("unavailable", "Video processing is shutting down.")
                job = _Job(str(uuid4()), owner, filename, directory, self._clock())
                self._jobs[job.job_id] = job
                accepted = True
                self._condition.notify_all()
                return self._status(job)
        except VideoJobError:
            raise
        except Exception:
            logger.exception("Unable to store temporary video upload")
            raise VideoJobError("upload_failed", "Unable to store the video upload.") from None
        finally:
            with self._condition:
                if directory is not None and not accepted:
                    self._remove_directory(directory)
                self._reservations -= 1
                self._condition.notify_all()

    def status(self, job_id: str, owner: str) -> dict[str, object]:
        with self._condition:
            return self._status(self._owned(job_id, owner))

    def result(self, job_id: str, owner: str) -> dict[str, object]:
        with self._condition:
            job = self._owned(job_id, owner)
            if job.state != "completed" or job.preview is None:
                raise VideoJobError("not_completed", "No completed video preview is available.")
            return {
                "job_id": job.job_id, **deepcopy(job.preview),
                "expires_at": _iso(job.expires_at),
            }

    def discard(self, job_id: str, owner: str) -> None:
        with self._condition:
            job = self._owned(job_id, owner)
            if job.state == "processing":
                raise VideoJobError("processing", "Active video processing cannot be discarded.")
            self._remove_directory(job.directory)
            del self._jobs[job_id]
            self._condition.notify_all()

    def cleanup(self) -> None:
        with self._condition:
            self._cleanup_locked()

    def shutdown(self) -> None:
        """Stop admission, discard queued work, wait for active work, clear jobs."""
        with self._condition:
            self._accepting = False
            for job_id, job in list(self._jobs.items()):
                if job.state == "queued":
                    self._remove_directory(job.directory)
                    del self._jobs[job_id]
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join()
        with self._condition:
            self._condition.wait_for(lambda: self._reservations == 0)
            for job in self._jobs.values():
                self._remove_directory(job.directory)
            self._jobs.clear()
            self._cleanup_locked()

    def _owned(self, job_id: str, owner: str) -> _Job:
        self._cleanup_locked()
        job = self._jobs.get(job_id)
        if job is None or job.owner != owner:
            raise VideoJobError("not_found", "Video job is unavailable or has expired.")
        return job

    @staticmethod
    def _status(job: _Job) -> dict[str, object]:
        return {
            "job_id": job.job_id, "filename": job.filename, "state": job.state,
            "created_at": _iso(job.created_at), "expires_at": _iso(job.expires_at),
            "error": deepcopy(job.error),
        }

    def _remove_directory(self, directory: Path) -> None:
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            self._pending_cleanup.discard(directory)
        except OSError:
            self._pending_cleanup.add(directory)
            logger.exception("Temporary video directory cleanup failed; will retry")
        else:
            self._pending_cleanup.discard(directory)

    def _cleanup_locked(self) -> None:
        for directory in list(self._pending_cleanup):
            self._remove_directory(directory)
        now = self._clock()
        for job_id, job in list(self._jobs.items()):
            if job.state in {"completed", "failed"} and (
                job.expires_at is not None and job.expires_at <= now
            ):
                self._remove_directory(job.directory)
                del self._jobs[job_id]

    def _run(self) -> None:
        while True:
            with self._condition:
                self._cleanup_locked()
                if not self._accepting:
                    return
                job = next((job for job in self._jobs.values() if job.state == "queued"), None)
                if job is None:
                    self._condition.wait(timeout=30)
                    continue
                job.state = "processing"
            self._process(job)
            del job

    def _process(self, job: _Job) -> None:
        preview = None
        error = None
        try:
            extracted = self._extractor(job.directory / job.filename)
            preview = _preview(extracted, job.filename)
        except VideoJobError as failure:
            logger.warning("Video result rejected for job %s: %s", job.job_id, failure.code)
            error = {"code": failure.code, "message": str(failure)}
        except Exception:
            logger.exception("Video extraction failed for job %s", job.job_id)
            error = {
                "code": "extraction_failed",
                "message": "Video extraction failed. Check the recording and local extraction setup.",
            }
        finally:
            with self._condition:
                self._remove_directory(job.directory)
                job.preview = preview
                job.error = error
                job.state = "failed" if error is not None else "completed"
                job.expires_at = self._clock() + RETENTION_SECONDS
                self._condition.notify_all()
