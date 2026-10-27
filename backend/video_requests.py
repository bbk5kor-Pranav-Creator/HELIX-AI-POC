import math
import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from fastapi import HTTPException
from openai import APIConnectionError, APIStatusError


def setting(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        raise HTTPException(status_code=503, detail=f"{name} must be between {minimum:g} and {maximum:g}.") from None


def seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        units = {"ms": .001, "s": 1, "m": 60, "h": 3600}
        matches = list(re.finditer(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value))
        if not matches or "".join(match.group() for match in matches) != value:
            return 0
        number = sum(float(match[1]) * units[match[2]] for match in matches)
    return max(0, number) if math.isfinite(number) else 0


def cooldown(headers) -> float:
    headers = {key.lower(): value for key, value in headers.items()}
    delay = seconds(headers.get("retry-after", "0"))
    if not delay and headers.get("retry-after"):
        try:
            date = parsedate_to_datetime(headers["retry-after"])
            delay = max(0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            delay = 0
    delay = max(delay, seconds(headers.get("retry-after-ms", "0")) / 1000)
    for kind in ("requests", "tokens"):
        if headers.get(f"x-ratelimit-remaining-{kind}") == "0":
            delay = max(delay, seconds(headers.get(f"x-ratelimit-reset-{kind}", "0")))
    return delay


class VisionRequests:
    def __init__(self, state: dict):
        self.state = state
        self.interval = setting("GROQ_VIDEO_REQUEST_INTERVAL_SECONDS", 4, 1, 120)
        retries = setting("GROQ_VIDEO_MAX_RETRIES", 3, 0, 5)
        if not retries.is_integer():
            raise HTTPException(status_code=503, detail="GROQ_VIDEO_MAX_RETRIES must be an integer between 0 and 5.")
        self.retries = int(retries)
        self.wait_budget = setting("GROQ_VIDEO_RETRY_WAIT_SECONDS", 120, 0, 300)
        self.last_call = 0.0
        self.last_headers = {}

    def call(self, operation, frame: dict):
        waited = 0.0
        for attempt in range(self.retries + 1):
            delay = max(
                0, self.state.get("not_before", 0) - time.time(),
                self.state.get("last_request_at", 0) + self.interval - time.time(),
                self.last_call + self.interval - time.monotonic(),
            )
            if delay > self.wait_budget - waited:
                raise HTTPException(status_code=429, detail="Provider cooldown exceeds this frame's wait budget. Resume later; completed frames are retained.")
            time.sleep(delay)
            waited += delay
            self.last_call = time.monotonic()
            self.state["last_request_at"] = time.time()
            if frame.get("request_attempts", 0):
                frame["retry_attempts"] = frame.get("retry_attempts", 0) + 1
            frame["request_attempts"] = frame.get("request_attempts", 0) + 1
            try:
                response = operation()
                self.observe(response.headers)
                result = response.parse()
                self.reserve_next_request(result)
                return result
            except (APIConnectionError, APIStatusError) as error:
                transient = isinstance(error, APIConnectionError) or error.status_code in {408, 409, 429, 500, 502, 503, 504}
                if isinstance(error, APIStatusError):
                    self.observe(error.response.headers)
                if not transient:
                    raise
                # Preserve provider cooldown for a later Resume, even on the final attempt.
                backoff = min(30, 2 ** (attempt + 1)) + random.uniform(0, 1)
                self.state["not_before"] = max(self.state.get("not_before", 0), time.time() + backoff)
                if attempt == self.retries:
                    limited = isinstance(error, APIStatusError) and error.status_code == 429
                    raise HTTPException(status_code=429 if isinstance(error, APIStatusError) and error.status_code == 429 else 502,
                                        detail=f"Vision {'rate-limit (HTTP 429)' if limited else 'transient-error'} retry budget exhausted. Resume unfinished frames later.") from None
                next_delay = max(0, self.state["not_before"] - time.time())
                if next_delay > self.wait_budget - waited:
                    raise HTTPException(status_code=429, detail="Provider retry-after exceeds the bounded wait budget. Resume unfinished frames after the cooldown.") from None
        raise AssertionError("Unreachable retry state")

    def observe(self, headers):
        self.last_headers = {
            key.lower(): value for key, value in headers.items()
            if key.lower().startswith(("x-ratelimit-", "retry-after"))
        }
        self.state.setdefault("rate_limits", {}).update(self.last_headers)
        self.state["not_before"] = max(
            self.state.get("not_before", 0), time.time() + cooldown(headers),
        )

    def reserve_next_request(self, response):
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        if not isinstance(prompt_tokens, int):
            return
        remaining = self.last_headers.get("x-ratelimit-remaining-tokens", "")
        if remaining.isdigit() and int(remaining) < prompt_tokens + 1200:
            reset = seconds(self.last_headers.get("x-ratelimit-reset-tokens", "0"))
            self.state["not_before"] = max(self.state.get("not_before", 0), time.time() + reset)
