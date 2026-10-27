import json
import hashlib
import os
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

import knowledge_store
from audio_routes import AudioSubmission
from knowledge_routes import require_admin
from video_media import MAX_VIDEO_BYTES, VIDEO_TYPES, clean_filename, tool
from video_processing import process_video
from video_checkpoints import checkpoint


router = APIRouter()
processing_slot = threading.BoundedSemaphore(1)


class VideoStreamResponse(StreamingResponse):
    def __init__(self, content, cleanup):
        super().__init__(
            content, media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # A disconnect during response headers may never start the generator.
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await self.cleanup()


def next_event(iterator):
    return next(iterator, None)


@router.post("/api/knowledge/video/process")
def extract_video(
    request: Request, file: UploadFile = File(...),
    resume_id: Annotated[str | None, Form(max_length=36)] = None,
    _user=Depends(require_admin),
):
    with ExitStack() as resources:
        resources.callback(file.file.close)
        filename = clean_filename(file.filename or "")
        suffix = Path(filename).suffix.lower()
        mime = (file.content_type or "").split(";", 1)[0].strip().lower()
        if mime not in VIDEO_TYPES[suffix] | {"", "application/octet-stream"}:
            raise HTTPException(status_code=415, detail="Video file type does not match its extension.")
        if file.size is not None and file.size > MAX_VIDEO_BYTES:
            raise HTTPException(status_code=413, detail="Video must be 500 MB or smaller.")
        header = file.file.read(12)
        file.file.seek(0)
        if not header:
            raise HTTPException(status_code=422, detail="Video file is empty.")
        atoms = {b"ftyp"} | ({b"moov", b"mdat", b"wide", b"free"} if suffix == ".mov" else set())
        valid_header = header.startswith(b"\x1a\x45\xdf\xa3") if suffix == ".webm" else header[4:8] in atoms
        if not valid_header:
            raise HTTPException(status_code=415, detail="File content does not match the video format.")
        tool("ffmpeg")
        tool("ffprobe")
        if not processing_slot.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="Another video is processing. Please wait and retry.", headers={"Retry-After": "10"})
        resources.callback(processing_slot.release)
        directory = tempfile.TemporaryDirectory(prefix="helix-video-")
        resources.enter_context(directory)
        path = Path(directory.name) / f"upload{suffix}"
        size = 0
        digest = hashlib.sha256()
        with path.open("wb") as target:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > MAX_VIDEO_BYTES:
                    raise HTTPException(status_code=413, detail="Video must be 500 MB or smaller.")
                target.write(block)
                digest.update(block)
        if not size:
            raise HTTPException(status_code=422, detail="Video file is empty.")
        file.file.close()
        resume_id, state = checkpoint(
            _user["sub"], digest.hexdigest(), resume_id,
            os.getenv("GROQ_VISION_MODEL", "").strip(),
        )
        response_resources = resources.pop_all()

    iterator = process_video(path, filename, Path(directory.name), state)
    cleaned = False

    async def cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        with anyio.CancelScope(shield=True):
            try:
                await run_in_threadpool(iterator.close)
            finally:
                await run_in_threadpool(response_resources.close)

    async def stream():
        try:
            yield json.dumps({
                "stage": "checkpoint", "resume_id": resume_id,
                "message": "Checkpoint active for up to one hour. Resume requires the same video and Admin account.",
            }) + "\n"
            while not await request.is_disconnected():
                event = await run_in_threadpool(next_event, iterator)
                if event is None:
                    break
                if event["stage"] == "complete":
                    event["resume_id"] = resume_id
                yield json.dumps(event) + "\n"
        except HTTPException as error:
            yield json.dumps({"stage": "error", "message": error.detail}) + "\n"
        except OSError:
            yield json.dumps({"stage": "error", "message": "Local video processing failed. Check available temporary disk space and media tool permissions."}) + "\n"
        finally:
            # A disconnected client stops the next stage; an in-flight tool/API call
            # finishes within its timeout before its resources can be safely removed.
            await cleanup()

    return VideoStreamResponse(stream(), cleanup)


@router.post("/api/knowledge/video")
def confirm_video(request: AudioSubmission, _user=Depends(require_admin)):
    filename = clean_filename(request.filename)
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="Video knowledge must not be blank.")
    source = knowledge_store.save_text(
        (request.title or "").strip() or filename[:120],
        request.text, request.notes, source_type="Video", filename=filename,
    )
    return {"success": True, "source": source}
