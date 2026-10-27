import os
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import Field

import knowledge_store
from knowledge_routes import TextSubmission, require_admin
from groq_transport import groq_http_options


router = APIRouter()
MAX_AUDIO_BYTES = 25_000_000
AUDIO_TYPES = {
    ".mp3": {"audio/mpeg", "audio/mp3"},
    ".wav": {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"},
    ".m4a": {"audio/mp4", "audio/m4a", "audio/x-m4a", "video/mp4"},
    ".webm": {"audio/webm", "video/webm"},
}


def clean_filename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise HTTPException(status_code=422, detail="Invalid audio filename.")
    if PurePosixPath(name).suffix.lower() not in AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="Use MP3, WAV, M4A, or WebM audio.")
    return name


def matches_audio_header(extension: str, data: bytes) -> bool:
    if extension == ".mp3":
        return data.startswith(b"ID3") or (
            len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        )
    if extension == ".wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if extension == ".m4a":
        return data[4:8] == b"ftyp"
    return data.startswith(b"\x1a\x45\xdf\xa3")


class AudioSubmission(TextSubmission):
    filename: str = Field(..., min_length=1, max_length=255)


def transcribe_content(filename: str, data: bytes) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Audio transcription is not configured.")
    try:
        # Disable retries to avoid duplicate paid transcription requests.
        with OpenAI(
            api_key=api_key, base_url="https://api.groq.com/openai/v1",
            timeout=120.0, max_retries=0,
            **groq_http_options(),
        ) as client:
            result = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=(filename, data),
                response_format="json",
            )
    except APITimeoutError:
        raise HTTPException(status_code=504, detail="Transcription timed out. Please retry.") from None
    except APIConnectionError:
        raise HTTPException(status_code=502, detail="Unable to reach the transcription service.") from None
    except APIStatusError as error:
        if error.status_code == 429:
            raise HTTPException(status_code=429, detail="Transcription service is busy. Try again later.") from None
        if error.status_code in {400, 413, 415, 422}:
            raise HTTPException(status_code=422, detail="The service could not decode this audio. Check the recording and try again.") from None
        raise HTTPException(status_code=502, detail="Transcription service failed. Please try again later.") from None
    return result.text.strip()


@router.post("/api/knowledge/audio/transcribe")
def transcribe_audio(file: UploadFile = File(...), _user=Depends(require_admin)):
    try:
        filename = clean_filename(file.filename or "")
        extension = PurePosixPath(filename).suffix.lower()
        content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in AUDIO_TYPES[extension] | {"", "application/octet-stream"}:
            raise HTTPException(status_code=415, detail="Audio file type does not match its extension.")
        if file.size is not None and file.size > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio must be 25 MB or smaller.")
        data = file.file.read(MAX_AUDIO_BYTES + 1)
        if not data:
            raise HTTPException(status_code=422, detail="Audio file is empty.")
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio must be 25 MB or smaller.")
        if not matches_audio_header(extension, data):
            raise HTTPException(status_code=415, detail="File content does not match the audio format.")
        text = transcribe_content(filename, data)
        if not text:
            raise HTTPException(status_code=422, detail="No speech was found in this recording.")
        if len(text) > 200_000:
            raise HTTPException(status_code=422, detail="Transcript exceeds the 200,000 character limit. Use a shorter recording.")
        return {"text": text, "filename": filename}
    finally:
        # UploadFile owns the framework's temporary/spooled file.
        file.file.close()


@router.post("/api/knowledge/audio")
def confirm_audio(request: AudioSubmission, _user=Depends(require_admin)):
    filename = clean_filename(request.filename)
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="Transcript must not be blank.")
    source = knowledge_store.save_text(
        (request.title or "").strip() or filename[:120],
        request.text, request.notes, source_type="Audio", filename=filename,
    )
    return {"success": True, "source": source}
