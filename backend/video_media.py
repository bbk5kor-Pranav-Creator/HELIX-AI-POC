import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from fastapi import HTTPException


MAX_VIDEO_BYTES = 500_000_000
MAX_DURATION = 1200
VIDEO_TYPES = {
    ".mp4": {"video/mp4"},
    ".mov": {"video/quicktime"},
    ".webm": {"video/webm"},
}
SEGMENT_SECONDS = 180
MAX_UNIFORM_FRAMES = 24
MAX_SCENE_FRAMES = 12


def clean_filename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise HTTPException(status_code=422, detail="Invalid video filename.")
    if PurePosixPath(name).suffix.lower() not in VIDEO_TYPES:
        raise HTTPException(status_code=415, detail="Use MP4, MOV, or WebM video.")
    return name


def tool(name: str) -> str:
    executable = os.getenv(f"{name.upper()}_PATH") or shutil.which(name)
    if not executable or not Path(executable).is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Install {name} and add it to PATH, or set {name.upper()}_PATH in backend/.env.",
        )
    return executable


def run_media(arguments: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=422, detail="Video processing timed out. Use a shorter recording.") from None
    except OSError:
        raise HTTPException(status_code=503, detail="Unable to start the configured media processing tool.") from None
    if result.returncode:
        raise HTTPException(status_code=422, detail="Video is corrupted or uses an unsupported codec. Re-export it as MP4 (H.264/AAC).")
    return result


def input_args(path: Path) -> list[str]:
    # Only local container files, never playlists or external resource references.
    return ["-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm", "-i", str(path)]


def probe_video(path: Path) -> dict:
    result = run_media([
        tool("ffprobe"), "-v", "error", *input_args(path),
        "-show_entries", "format=duration,format_name:stream=codec_type,width,height",
        "-of", "json",
    ], timeout=30)
    try:
        metadata = json.loads(result.stdout)
        duration = float(metadata["format"]["duration"])
        streams = metadata["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        width, height = int(video["width"]), int(video["height"])
    except (ValueError, KeyError, TypeError, StopIteration):
        raise HTTPException(status_code=422, detail="Cannot read video duration or video stream.") from None
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise HTTPException(status_code=422, detail="Video must have a valid duration of at most 20 minutes.")
    if width <= 0 or height <= 0 or width * height > 3840 * 2160:
        raise HTTPException(status_code=422, detail="Video resolution must be at most 3840 x 2160 pixels.")
    return {"duration": duration, "has_audio": any(s["codec_type"] == "audio" for s in streams)}


def extract_audio(path: Path, directory: Path) -> list[Path]:
    run_media([
        tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1",
        *input_args(path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "flac", "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
        "-reset_timestamps", "1", str(directory / "audio-%03d.flac"),
    ])
    segments = sorted(directory.glob("audio-*.flac"))
    if not segments:
        raise HTTPException(status_code=422, detail="Unable to extract the video's audio track.")
    return segments


def is_quiet(path: Path) -> bool:
    result = run_media([
        tool("ffmpeg"), "-nostdin", "-hide_banner", "-threads", "1", "-i", str(path),
        "-af", "volumedetect", "-f", "null", "-",
    ], timeout=30)
    match = re.search(r"max_volume:\s*(-?inf|[-\d.]+)\s*dB", result.stderr)
    if not match:
        raise HTTPException(status_code=422, detail="Unable to assess the extracted audio volume.")
    return float(match.group(1)) <= -50


def frame_times(duration: float, scene_times: list[float]) -> list[float]:
    count = min(MAX_UNIFORM_FRAMES, max(1, math.ceil(duration / 15)))
    times = [min(duration - 0.01, index * duration / count) for index in range(count)]
    times.append(max(0, duration - min(1, duration / 2)))
    for timestamp in scene_times:
        if 0 <= timestamp < duration and all(abs(timestamp - other) >= 1 for other in times):
            times.append(timestamp)
    return sorted(set(round(max(0, value), 3) for value in times))


def find_scenes(path: Path) -> list[float]:
    result = run_media([
        tool("ffmpeg"), "-nostdin", "-hide_banner", "-threads", "1",
        *input_args(path), "-an", "-vf",
        "fps=2,scale=640:-2,select=gt(scene\\,0.10),showinfo",
        "-f", "null", "-",
    ])
    times = [float(value) for value in re.findall(r"pts_time:([\d.]+)", result.stderr)]
    if len(times) <= MAX_SCENE_FRAMES:
        return times
    return [times[round(index * (len(times) - 1) / (MAX_SCENE_FRAMES - 1))]
            for index in range(MAX_SCENE_FRAMES)]


def extract_frame(path: Path, timestamp: float, target: Path) -> None:
    run_media([
        tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-threads", "1", "-ss", str(timestamp), *input_args(path), "-an",
        "-frames:v", "1", "-vf", "scale=1600:1600:force_original_aspect_ratio=decrease",
        "-q:v", "3", str(target),
    ], timeout=30)
    if not target.is_file() or not 0 < target.stat().st_size <= 4_000_000:
        raise HTTPException(status_code=422, detail="Unable to extract a usable video frame.")


def frame_pixels(path: Path) -> bytes:
    try:
        result = subprocess.run([
            tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-i", str(path),
            "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(status_code=422, detail="Frame comparison failed or timed out.") from None
    if result.returncode or len(result.stdout) != 320 * 180:
        raise HTTPException(status_code=422, detail="Unable to compare selected frames.")
    return result.stdout


def same_frame(first: bytes, second: bytes) -> bool:
    if len(first) != len(second) or not first:
        return False
    differences = [abs(a - b) for a, b in zip(first, second)]
    # Conservative: retain tiny UI changes rather than over-collapse distinct states.
    return sum(value > 12 for value in differences) <= len(first) * .0002 and sum(differences) <= len(first) * .15


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
