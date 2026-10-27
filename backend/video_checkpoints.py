import threading
import time
from uuid import uuid4

from fastapi import HTTPException


TTL_SECONDS = 3600
MAX_CHECKPOINTS = 4
_lock = threading.Lock()
_entries = {}


def checkpoint(owner: str, digest: str, resume_id: str | None, model: str):
    now = time.time()
    with _lock:
        for key in list(_entries):
            if now - _entries[key]["updated_at"] > TTL_SECONDS:
                del _entries[key]
        if resume_id:
            state = _entries.get(resume_id)
            if not state or state["owner"] != owner:
                raise HTTPException(status_code=410, detail="Video checkpoint expired or is unavailable in this worker. Start a new extraction.")
            if state["digest"] != digest:
                raise HTTPException(status_code=409, detail="Resume requires the exact same video file.")
            if state["model"] != model:
                raise HTTPException(status_code=409, detail="Vision model changed. Start a new extraction to avoid mixing model results.")
        else:
            if len(_entries) >= MAX_CHECKPOINTS:
                oldest = min(_entries, key=lambda key: _entries[key]["updated_at"])
                del _entries[oldest]
            resume_id = str(uuid4())
            state = {
                "owner": owner, "digest": digest, "model": model,
                "frames": {}, "speech": [], "speech_warnings": [], "speech_done": False,
            }
            _entries[resume_id] = state
        state["updated_at"] = now
        return resume_id, state


def touch(state: dict):
    with _lock:
        state["updated_at"] = time.time()
