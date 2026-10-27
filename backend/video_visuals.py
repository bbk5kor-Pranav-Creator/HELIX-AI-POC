import time
from contextlib import ExitStack
from pathlib import Path

from fastapi import HTTPException

from video_checkpoints import touch
from video_media import extract_frame, frame_pixels, same_frame, timestamp
from video_requests import VisionRequests
from video_vision import check_model, describe_frame, groq_client, ocr_frame


def timeline_order(times):
    if not times:
        return []
    # Spread a limited quota over the timeline before filling chronological gaps.
    result = [times[0]]
    if len(times) > 1:
        result.append(times[-1])
    queue = [times[1:-1]]
    while queue:
        part = queue.pop(0)
        if part:
            middle = len(part) // 2
            result.append(part[middle])
            queue.extend([part[:middle], part[middle + 1:]])
    return result


def analyze_visuals(path: Path, directory: Path, duration: float, state: dict, warnings: list):
    frames = state["frames"]
    times = state["times"]
    ocr_enabled = True
    targets = {}
    # Local scan first: duplicate selection and OCR cover the timeline even if Groq is limited.
    for index, seconds in enumerate(times):
        frame = frames.setdefault(seconds, {})
        if frame.get("vision_done") or frame.get("duplicate_of") is not None:
            continue
        frame.pop("error", None)
        yield {"stage": "analyzing_frames", "message": f"Selecting/OCR frame {index + 1} of {len(times)} at {timestamp(seconds)}..."}
        target = directory / f"frame-{index:03d}.jpg"
        try:
            extract_frame(path, seconds, target)
            targets[seconds] = target
            pixels = frame_pixels(target)
            duplicate = next((
                other for other in times if other < seconds and frames.get(other, {}).get("pixels")
                and frames[other].get("duplicate_of") is None
                and same_frame(pixels, frames[other]["pixels"])
            ), None)
            frame["pixels"] = pixels
            frame.pop("error", None)
            if duplicate is not None:
                frame["duplicate_of"] = duplicate
                continue
        except HTTPException as error:
            # A comparison failure must not discard a successfully decoded frame.
            if seconds not in targets:
                frame["error"] = f"Frame at {timestamp(seconds)} could not be extracted: {error.detail}"
                continue
            warnings.append(f"Duplicate comparison unavailable at {timestamp(seconds)}: {error.detail}")
        if ocr_enabled and "ocr" not in frame:
            try:
                frame["ocr"] = ocr_frame(target)
                state["ocr_status"] = "available"
                state.pop("ocr_error", None)
            except HTTPException as error:
                ocr_enabled = False
                state["ocr_status"] = "partial" if state.get("ocr_status") == "available" else "unavailable"
                state["ocr_error"] = error.detail
                warnings.append(error.detail)
        touch(state)
    if state.get("ocr_error"):
        warnings.append(state["ocr_error"])
    requests = VisionRequests(state)
    with ExitStack() as stack:
        try:
            client = stack.enter_context(groq_client())
            model = check_model(client)
        except HTTPException as error:
            state["vision_error"] = f"Vision unavailable: {error.detail}"
            return
        state.pop("vision_error", None)
        for seconds in timeline_order(times):
            frame = frames[seconds]
            if frame.get("vision_done") or frame.get("duplicate_of") is not None or seconds not in targets:
                continue
            yield {"stage": "analyzing_frames", "message": f"Interpreting instructional frame at {timestamp(seconds)} (bounded retries enabled)..."}
            previous = [t for t, f in frames.items() if t < seconds and f.get("vision_done") and f.get("vision")]
            earlier = max(previous) if previous else None
            context = f"Timestamp: {timestamp(seconds)} / {timestamp(duration)}."
            if earlier is not None:
                context += f"\nPrevious observed screen at {timestamp(earlier)}:\n{frames[earlier]['vision'][:2500]}"
            speech = [text for start, text in state["speech"] if start <= seconds < start + 180]
            context += "\nNearby speech (context, not visible evidence):\n" + "\n".join(speech)[:2000]
            try:
                frame["vision"] = describe_frame(
                    client, model, targets[seconds], requests=requests, frame=frame, context=context,
                )
                frame["vision_done"] = True
                frame.pop("error", None)
            except HTTPException as error:
                frame["error"] = f"Vision unfinished at {timestamp(seconds)}: {error.detail}"
                state["vision_error"] = frame["error"]
                # Do not burst through other frames when the shared account quota is exhausted.
                break
            finally:
                touch(state)
    if state.get("vision_error"):
        warnings.append(state["vision_error"])


def coverage_report(duration: float, state: dict) -> dict:
    frames = state["frames"]
    times = state["times"]
    analyzed = [t for t in times if frames.get(t, {}).get("vision_done")]
    duplicates = [t for t in times if frames.get(t, {}).get("duplicate_of") is not None]
    unfinished = [t for t in times if not frames.get(t, {}).get("vision_done") and t not in duplicates]
    reasons = list(dict.fromkeys(
        [frames[t]["error"] for t in times if frames.get(t, {}).get("error")]
        + ([state["vision_error"]] if state.get("vision_error") else [])
        + ([state["sampling_warning"]] if state.get("sampling_warning") else [])
    ))
    if unfinished:
        reasons.append(f"{len(unfinished)} unique selected frame(s) have no completed vision interpretation.")
    return {
        "duration_seconds": duration, "selected": len(times), "analyzed": len(analyzed),
        "retried": sum(bool(frame.get("retry_attempts")) for frame in frames.values()),
        "retry_attempts": sum(frame.get("retry_attempts", 0) for frame in frames.values()),
        "skipped": len(times) - len(analyzed), "duplicates": len(duplicates),
        "ocr_only": sum(bool(f.get("ocr")) and not f.get("vision_done") and f.get("duplicate_of") is None for f in frames.values()),
        "analyzed_timestamps": analyzed, "unfinished_timestamps": unfinished,
        "first_analyzed": timestamp(min(analyzed)) if analyzed else None,
        "last_analyzed": timestamp(max(analyzed)) if analyzed else None,
        "ocr_status": state.get("ocr_status", "not_checked"),
        "visual_coverage": "partial" if unfinished or state.get("sampling_warning") else "selected_samples_complete",
        "full_video_coverage": False, "incomplete_reasons": reasons,
        "can_resume": bool(unfinished),
        "retry_after_seconds": max(0, round(state.get("not_before", 0) - time.time())),
        "provider_rate_limits": state.get("rate_limits", {}),
    }
