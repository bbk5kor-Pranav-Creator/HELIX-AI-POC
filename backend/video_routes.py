"""Admin video API adapter.

Include ``router`` in the application to activate its registry lifespan. Run one
Uvicorn worker only. Confirmation receipts last only as long as their in-memory
jobs; neither jobs nor receipts survive a restart. No extraction occurs here.
The registry reports expired jobs as unavailable (404) and does not renew their
retention on confirmation. Same-job idempotency is not global video deduplication.
"""

import hashlib
import json
import logging
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

import knowledge_store
from knowledge_routes import TextSubmission, require_admin
from video_jobs import MAX_JOBS, MAX_UPLOAD_BYTES, VideoJobError, VideoJobRegistry


logger = logging.getLogger(__name__)
VIDEO_TYPES = {
    ".mp4": {"video/mp4"},
    ".mov": {"video/quicktime"},
    ".webm": {"video/webm"},
}
NO_STORE = {"Cache-Control": "no-store"}


class VideoSubmission(TextSubmission):
    job_id: UUID


class _NoStoreRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await handler(request)
            except StarletteHTTPException as error:
                response = JSONResponse(
                    {"detail": error.detail}, status_code=error.status_code,
                    headers=error.headers,
                )
            except RequestValidationError as error:
                response = JSONResponse(
                    {"detail": jsonable_encoder(error.errors())}, status_code=422,
                )
            except Exception:
                logger.exception("Unexpected video API failure")
                response = JSONResponse(
                    {"detail": "Video operation failed. Please try again later."},
                    status_code=500,
                )
            response.headers.update(NO_STORE)
            return response

        return handle


def _job_error(error: VideoJobError) -> HTTPException:
    statuses = {
        "invalid_filename": 422, "unsupported_video": 415, "invalid_owner": 422,
        "capacity": 429, "upload_too_large": 413, "empty_upload": 422,
        "unavailable": 503, "registry_stopped": 503, "upload_failed": 500,
        "not_found": 404, "not_completed": 409, "processing": 409,
    }
    return HTTPException(
        status_code=statuses.get(error.code, 500),
        detail=str(error) if error.code in statuses else "Video operation failed.",
        headers={"Retry-After": "10"} if error.code == "capacity" else None,
    )


def _filename(value: str) -> str:
    name = PurePosixPath(value.replace("\\", "/")).name
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    if (
        not name or len(name) > 255 or name.endswith((" ", "."))
        or any(ord(char) < 32 or char in '<>:"|?*' for char in name)
        or name.split(".", 1)[0].upper() in reserved
    ):
        raise HTTPException(status_code=422, detail="Invalid video filename.")
    if PurePosixPath(name).suffix.lower() not in VIDEO_TYPES:
        raise HTTPException(status_code=415, detail="Use MP4, MOV, or WebM video.")
    return name


def _upload_limit() -> int:
    try:
        configured = int(os.getenv("VIDEO_MAX_UPLOAD_MB", "500"))
        if configured <= 0:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=503, detail="The video upload limit is not configured correctly.",
        ) from None
    return min(MAX_UPLOAD_BYTES, configured * 1024 * 1024)


def _tool(name: str) -> str:
    configured = os.getenv(f"{name.upper()}_PATH")
    executable = configured or shutil.which(name)
    if not executable or not Path(executable).is_file():
        raise HTTPException(
            status_code=503, detail=f"The local {name} executable is unavailable.",
        )
    return executable


def _probe(path: Path, extension: str) -> None:
    try:
        result = subprocess.run(
            [
                _tool("ffprobe"), "-v", "error", "-protocol_whitelist", "file",
                "-format_whitelist", "mov,matroska,webm", "-i", str(path),
                "-show_entries", "format=duration,format_name:stream=codec_type,width,height",
                "-of", "json",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=422, detail="Video metadata validation timed out.") from None
    except OSError:
        logger.exception("Unable to start FFprobe")
        raise HTTPException(status_code=503, detail="Local video validation is unavailable.") from None
    if result.returncode:
        raise HTTPException(status_code=422, detail="The video could not be decoded.")
    try:
        metadata = json.loads(result.stdout)
        duration = float(metadata["format"]["duration"])
        formats = set(metadata["format"]["format_name"].split(","))
        streams = metadata["streams"]
        videos = [stream for stream in streams if stream["codec_type"] == "video"]
        if not videos or not math.isfinite(duration) or not 0 < duration <= 1200:
            raise ValueError
        if extension == ".webm":
            if not formats.intersection({"matroska", "webm"}):
                raise ValueError
        elif not formats.intersection({"mov", "mp4"}):
            raise ValueError
        for video in videos:
            width, height = video["width"], video["height"]
            if (
                type(width) is not int or type(height) is not int
                or not 0 < width <= 3840 or not 0 < height <= 2160
            ):
                raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise HTTPException(
            status_code=422,
            detail="Video must contain a valid video stream, be at most 20 minutes, and at most 3840 x 2160 pixels.",
        ) from None


def _validate_upload(file: UploadFile, target: Path, filename: str) -> None:
    limit = _upload_limit()
    extension = PurePosixPath(filename).suffix.lower()
    mime = (file.content_type or "").split(";", 1)[0].strip().lower()
    if mime not in VIDEO_TYPES[extension] | {"", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Video MIME type does not match its extension.")
    if file.size is not None and file.size > limit:
        raise HTTPException(status_code=413, detail=f"Video must be {limit:,} bytes or smaller.")
    size = 0
    with target.open("wb") as output:
        while block := file.file.read(1024 * 1024):
            size += len(block)
            if size > limit:
                raise HTTPException(status_code=413, detail=f"Video must be {limit:,} bytes or smaller.")
            output.write(block)
    if not size:
        raise HTTPException(status_code=422, detail="Video file is empty.")
    with target.open("rb") as source:
        header = source.read(12)
    atoms = {b"ftyp"} | ({b"moov", b"mdat", b"wide", b"free"} if extension == ".mov" else set())
    valid = header.startswith(b"\x1a\x45\xdf\xa3") if extension == ".webm" else (
        len(header) >= 8 and header[4:8] in atoms
    )
    if not valid:
        raise HTTPException(status_code=415, detail="Video content does not match its extension.")
    _tool("ffmpeg")
    _probe(target, extension)


@dataclass
class _Receipt:
    owner: str
    fingerprint: str
    source: dict[str, object]


def create_video_router(registry: VideoJobRegistry | None = None) -> APIRouter:
    """Create one router/registry pair; its lifespan owns worker start/shutdown."""
    jobs = registry if registry is not None else VideoJobRegistry()
    lock = threading.RLock()
    validation_slots = threading.BoundedSemaphore(MAX_JOBS)
    receipts: dict[str, _Receipt] = {}

    def prune_receipts() -> None:
        for job_id, receipt in list(receipts.items()):
            try:
                jobs.status(job_id, receipt.owner)
            except VideoJobError as error:
                if error.code != "not_found":
                    raise
                del receipts[job_id]

    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(jobs.start)
        try:
            yield
        finally:
            await run_in_threadpool(jobs.shutdown)
            with lock:
                receipts.clear()

    router = APIRouter(route_class=_NoStoreRoute, lifespan=lifespan)

    @router.post("/api/knowledge/video/process", status_code=202)
    def process_video(file: UploadFile = File(...), _user=Depends(require_admin)):
        acquired = False
        try:
            filename = _filename(file.filename or "")
            acquired = validation_slots.acquire(blocking=False)
            if not acquired:
                raise HTTPException(
                    status_code=429, detail="Video upload validation is busy. Try again later.",
                    headers={"Retry-After": "10"},
                )
            with tempfile.TemporaryDirectory(prefix="helix-video-validation-") as directory:
                path = Path(directory) / filename
                _validate_upload(file, path, filename)
                with lock:
                    prune_receipts()
                with path.open("rb") as source:
                    job = jobs.create(_user["sub"], filename, source)
            return {**job, "message": "Video accepted for local processing.", "poll_after_seconds": 2}
        except VideoJobError as error:
            raise _job_error(error) from None
        except OSError:
            logger.exception("Temporary video upload handling failed")
            raise HTTPException(status_code=500, detail="Unable to store the video upload.") from None
        finally:
            try:
                file.file.close()
            finally:
                if acquired:
                    validation_slots.release()

    @router.get("/api/knowledge/video/jobs/{job_id}")
    def get_job(job_id: UUID, _user=Depends(require_admin)):
        try:
            with lock:
                prune_receipts()
                status = jobs.status(str(job_id), _user["sub"])
                receipt = receipts.get(str(job_id))
                return {
                    **status, "saved": receipt is not None,
                    "source_id": receipt.source["id"] if receipt else None,
                    "poll_after_seconds": 2 if status["state"] in {"queued", "processing"} else None,
                }
        except VideoJobError as error:
            raise _job_error(error) from None

    @router.get("/api/knowledge/video/jobs/{job_id}/result")
    def get_result(job_id: UUID, _user=Depends(require_admin)):
        try:
            with lock:
                prune_receipts()
                return jobs.result(str(job_id), _user["sub"])
        except VideoJobError as error:
            raise _job_error(error) from None

    @router.post("/api/knowledge/video")
    def confirm_video(request: VideoSubmission, _user=Depends(require_admin)):
        if not request.text.strip():
            raise HTTPException(status_code=422, detail="Video knowledge must not be blank.")
        job_id = str(request.job_id)
        try:
            # Serialize save/retry/discard using only the registry's public API.
            with lock:
                prune_receipts()
                preview = jobs.result(job_id, _user["sub"])
                filename = str(preview["filename"])
                title = (request.title or "").strip() or filename[:120]
                fingerprint = hashlib.sha256(json.dumps(
                    [title, request.text, request.notes], ensure_ascii=True,
                ).encode("utf-8")).hexdigest()
                receipt = receipts.get(job_id)
                if receipt is not None:
                    if receipt.fingerprint != fingerprint:
                        raise HTTPException(
                            status_code=409, detail="This job has already been confirmed with different text.",
                        )
                    return {"success": True, "source": receipt.source}
                source = knowledge_store.save_text(
                    title, request.text, request.notes, source_type="Video", filename=filename,
                    metadata={
                        "video": preview["metadata"],
                        "transcript": preview["transcript"],
                        "video_sha256": preview["video_sha256"],
                    },
                )
                receipts[job_id] = _Receipt(_user["sub"], fingerprint, source)
                return {"success": True, "source": source}
        except VideoJobError as error:
            raise _job_error(error) from None
        except (sqlite3.Error, OSError):
            logger.exception("Unable to save confirmed video knowledge")
            raise HTTPException(status_code=500, detail="Unable to save video knowledge. Please retry.") from None

    @router.delete("/api/knowledge/video/jobs/{job_id}", status_code=204)
    def discard_job(job_id: UUID, _user=Depends(require_admin)):
        try:
            with lock:
                prune_receipts()
                jobs.discard(str(job_id), _user["sub"])
                receipts.pop(str(job_id), None)
            return Response(status_code=204)
        except VideoJobError as error:
            raise _job_error(error) from None

    return router


router = create_video_router()
