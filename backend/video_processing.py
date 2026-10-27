import time
from pathlib import Path

from fastapi import HTTPException

from audio_routes import MAX_AUDIO_BYTES, transcribe_content
from video_checkpoints import touch
from video_media import (
    SEGMENT_SECONDS, extract_audio, find_scenes, frame_times, is_quiet,
    probe_video, timestamp,
)
from video_requests import setting
from video_visuals import analyze_visuals, coverage_report


def request_interval() -> float:
    return setting("GROQ_VIDEO_REQUEST_INTERVAL_SECONDS", 4, 1, 120)


def process_video(path: Path, filename: str, directory: Path, state=None):
    state = state if state is not None else {
        "frames": {}, "speech": [], "speech_warnings": [], "speech_done": False,
    }
    yield {"stage": "validating", "message": "Validating video duration and streams..."}
    metadata = probe_video(path)
    duration = metadata["duration"]
    warnings = [
        "Timeline and scene samples are not continuous video coverage. Brief labels, actions, "
        "and transitions between samples may be missed; Admin verification is required."
    ]
    interval = request_interval()
    if not state["speech_done"]:
        speech = list(state["speech"])
        speech_warnings = []
        if metadata["has_audio"]:
            yield {"stage": "extracting_audio", "message": "Extracting mono audio in ordered 3-minute segments..."}
            try:
                segments = extract_audio(path, directory)
            except HTTPException as error:
                segments = []
                speech_warnings.append(f"Audio extraction failed; visual analysis continued: {error.detail}")
            quiet_audio = state.setdefault("quiet_audio", set())
            completed_audio = state.setdefault("audio_completed", set())
            for index, segment in enumerate(segments):
                if index in completed_audio:
                    continue
                yield {"stage": "transcribing", "message": f"Checking/transcribing audio segment {index + 1} of {len(segments)}..."}
                try:
                    if is_quiet(segment):
                        quiet_audio.add(index)
                        completed_audio.add(index)
                        continue
                    if segment.stat().st_size > MAX_AUDIO_BYTES:
                        raise HTTPException(status_code=422, detail="Extracted audio exceeds the Groq upload limit. Use a shorter recording.")
                    time.sleep(interval)
                    text = transcribe_content(segment.name, segment.read_bytes())
                    if text:
                        start = index * SEGMENT_SECONDS
                        end = min(duration, (index + 1) * SEGMENT_SECONDS)
                        speech.append((start, f"[{timestamp(start)} - {timestamp(end)}] Speech transcript\n{text}"))
                        state["speech"] = list(speech)
                    completed_audio.add(index)
                    touch(state)
                except HTTPException as error:
                    speech_warnings.append(f"Audio segment {index + 1} and remaining speech were not transcribed: {error.detail}")
                    break
            if quiet_audio:
                speech_warnings.append(f"{len(quiet_audio)} audio segment(s) were silent or below -50 dB peak and were not transcribed.")
            if not speech:
                speech_warnings.append("No speech transcript was obtained. Audio may be silent, unintelligible, or unavailable.")
        else:
            yield {"stage": "extracting_audio", "message": "No audio track found. Continuing with visual analysis."}
            speech_warnings.append("No audio track. Knowledge is based on sampled visual content only.")
        state.update(speech=speech, speech_warnings=speech_warnings, speech_done=True)
        touch(state)
    else:
        yield {"stage": "transcribing", "message": "Retaining the existing speech transcript; no successful speech calls repeated."}
    warnings.extend(state["speech_warnings"])
    if "times" not in state:
        yield {"stage": "analyzing_frames", "message": "Sampling the complete timeline and distributed scene changes..."}
        try:
            scenes = find_scenes(path)
        except HTTPException as error:
            scenes = []
            state["sampling_warning"] = f"Additional scene sampling unavailable: {error.detail} Timeline sampling continued."
        state["times"] = frame_times(duration, scenes)
    if state.get("sampling_warning"):
        warnings.append(state["sampling_warning"])
    yield from analyze_visuals(path, directory, duration, state, warnings)
    yield {"stage": "preparing_knowledge", "message": "Preparing chronological speech and instructional visual knowledge with coverage..."}
    coverage = coverage_report(duration, state)
    warnings.extend(coverage["incomplete_reasons"])
    observations = list(state["speech"])
    seen_visuals = {}
    for seconds, frame in sorted(state["frames"].items()):
        if frame.get("duplicate_of") is not None:
            original = state["frames"][frame["duplicate_of"]]
            if original.get("vision") or original.get("ocr"):
                observations.append((seconds, f"[{timestamp(seconds)}] Duplicate sampled screen, visually near-identical to {timestamp(frame['duplicate_of'])}; no new vision call. Small changes may be missed."))
            continue
        visual = frame.get("vision", "")
        ocr = frame.get("ocr", "")
        # Remove only OCR lines already present in vision; keep all remaining labels.
        normalized = " ".join(visual.casefold().split())
        ocr = "\n".join(line for line in ocr.splitlines() if " ".join(line.casefold().split()) not in normalized)
        parts = ([f"Vision interpretation:\n{visual}"] if visual else [])
        if ocr:
            parts.append(f"Local OCR (may contain recognition errors):\n{ocr}")
        if parts:
            combined = "\n\n".join(parts)
            if combined in seen_visuals:
                observations.append((seconds, f"[{timestamp(seconds)}] Repeated extracted instructional findings from {timestamp(seen_visuals[combined])}; independently interpreted sample, no additional steps reported."))
            else:
                observations.append((seconds, f"[{timestamp(seconds)}] Visual process/demo observation\n{combined}"))
                seen_visuals[combined] = seconds
    # Return even an empty partial preview so the Admin can resume unfinished frames.
    if not observations:
        warnings.append("No useful speech/OCR/visual knowledge was extracted. Resume unfinished frames or try a clearer recording; nothing can be confirmed yet.")
    summary = (
        f"Visual sample coverage: {coverage['visual_coverage']}. Selected: {coverage['selected']}; "
        f"vision analyzed: {coverage['analyzed']}; retried frames: {coverage['retried']}; "
        f"retry attempts: {coverage['retry_attempts']}; skipped: {coverage['skipped']}; "
        f"duplicates: {coverage['duplicates']}; OCR-only: {coverage['ocr_only']}.\n"
        f"First/last successfully vision analyzed: {coverage['first_analyzed'] or 'none'} / "
        f"{coverage['last_analyzed'] or 'none'}. OCR: {coverage['ocr_status']}.\n"
        f"Continuous full-video coverage: not guaranteed. Resume possible: {coverage['can_resume']}."
    )
    text = (
        f"Video: {filename}\nFilename: {filename}\nDuration: {timestamp(duration)}\n"
        "Process knowledge: chronological speech and sampled visual evidence\n\n" + summary +
        "\n\nCoverage / warnings:\n" + "\n".join(f"- {item}" for item in dict.fromkeys(warnings)) +
        "\n\n" + "\n\n".join(item for _, item in sorted(observations, key=lambda item: item[0]))
    )
    if len(text) > 200_000:
        raise HTTPException(status_code=422, detail="Extracted knowledge exceeds 200,000 characters. Split the video into shorter recordings.")
    touch(state)
    yield {
        "stage": "complete", "text": text, "filename": filename,
        "duration_seconds": duration, "sampled_timestamps": state["times"],
        "coverage": coverage, "has_knowledge": bool(observations),
        "warnings": list(dict.fromkeys(warnings)),
    }
