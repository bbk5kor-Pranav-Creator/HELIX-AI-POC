
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
from faster_whisper import WhisperModel


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.getenv("FFPROBE_PATH", "ffprobe")
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
MAX_UPLOAD_MB = int(os.getenv("VIDEO_MAX_UPLOAD_MB", "500"))

# Load the model only when extraction is requested.
_whisper_model = None


class VideoKnowledgeError(ValueError):
    """The video cannot provide usable speech knowledge."""


def _get_whisper_model():
    global _whisper_model

    if _whisper_model is None:
        started = time.perf_counter()
        _whisper_model = WhisperModel(
            WHISPER_MODEL_NAME,
            device="cpu",
            compute_type="int8",
        )
        logger.info(
            "Whisper model initialization completed in %.2f seconds",
            time.perf_counter() - started,
        )

    return _whisper_model


def _run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "Media command failed."
        )

    return result.stdout


def _get_metadata(video_path):
    output = _run_command(
        [
            FFPROBE_PATH,
            "-v", "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of", "json",
            str(video_path),
        ]
    )

    metadata = json.loads(output)
    streams = metadata.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        {},
    )

    duration = float(metadata.get("format", {}).get("duration", 0))

    return {
        "duration_seconds": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "has_audio": any(
            stream.get("codec_type") == "audio" for stream in streams
        ),
    }


def _extract_audio(video_path, audio_path):
    _run_command(
        [
            FFMPEG_PATH,
            "-nostdin",
            "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(audio_path),
        ]
    )


def _transcribe(audio_path):
    model = _get_whisper_model()
    started = time.perf_counter()

    segments, info = model.transcribe(
        str(audio_path),
        beam_size=3,
        vad_filter=True,
    )

    transcript = []

    for segment in segments:
        text = segment.text.strip()

        if text:
            transcript.append(
                {
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": text,
                }
            )

    result = {
        "language": info.language,
        "segments": transcript,
        "text": " ".join(item["text"] for item in transcript),
    }
    logger.info(
        "Whisper transcription completed in %.2f seconds",
        time.perf_counter() - started,
    )
    return result


def extract_video_knowledge(video_path):
    """Extract timestamped speech from a video."""

    video_path = Path(video_path).resolve()

    if not video_path.is_file():
        raise FileNotFoundError("The uploaded video was not found.")

    if video_path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(
            f"Video exceeds the {MAX_UPLOAD_MB} MB upload limit."
        )

    started = time.perf_counter()
    video_hash = hashlib.sha256()

    with video_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            video_hash.update(block)

    audio_prep_started = time.perf_counter()
    metadata = _get_metadata(video_path)

    if not metadata["has_audio"]:
        raise VideoKnowledgeError(
            "No audio track was found. Use a video containing speech."
        )

    with tempfile.TemporaryDirectory(prefix="helix_video_") as temp_dir:
        audio_path = Path(temp_dir) / "audio.wav"

        _extract_audio(video_path, audio_path)
        logger.info(
            "FFmpeg/audio preparation completed in %.2f seconds",
            time.perf_counter() - audio_prep_started,
        )

        transcript = _transcribe(audio_path)
        if not transcript["segments"]:
            raise VideoKnowledgeError(
                "No usable speech was extracted. Use a video containing clear speech."
            )

    elapsed = round(time.perf_counter() - started, 2)
    logger.info("Total video extraction completed in %.2f seconds", elapsed)

    return {
        "filename": video_path.name,
        "video_sha256": video_hash.hexdigest(),
        "metadata": metadata,
        "transcript": transcript,
        "processing_seconds": elapsed,
        "knowledge_text": transcript["text"],
    }
