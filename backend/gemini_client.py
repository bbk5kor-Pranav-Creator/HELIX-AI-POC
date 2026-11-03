import json
import logging
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, getproxies, urlopen


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
logger = logging.getLogger(__name__)
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|socks5h?)://[^\s]+")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(proxy-authorization|authorization|api[_-]?key|password|token)"
    r"\b\s*[:=]\s*[^\s,;]+"
)
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]+")
_GOOGLE_ERROR_STATUS_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_GATEWAY_SERVER_PATTERN = re.compile(
    r"(?i)(proxy|gateway|squid|envoy|nginx|haproxy|cloudflare|varnish|traefik)"
)


class GeminiTransportError(RuntimeError):
    """Gemini could not be reached over the configured HTTP transport."""


class GeminiResponseError(RuntimeError):
    """Gemini returned a response that could not be consumed safely."""


class GeminiHTTPError(RuntimeError):
    """Gemini rejected a request; response content is kept out of the message."""

    def __init__(self, status_code: int, response_body: bytes, *, headers=None):
        super().__init__(f"Gemini returned HTTP {status_code}.")
        self.code = status_code
        self.response_body = response_body[:16_384]
        self.server = _safe_header(headers, "Server")
        self.via = _safe_header(headers, "Via")
        self.content_type = _safe_header(headers, "Content-Type")
        self.google_error_status, self.google_error_message = _google_error_fields(
            self.response_body, status_code
        )
        self.origin = _classify_http_origin(
            self.server,
            self.via,
            self.content_type,
            self.google_error_status,
        )

    def read(self, size=-1):
        return self.response_body if size is None or size < 0 else self.response_body[:size]


def _https_proxy_configured() -> bool:
    try:
        proxies = getproxies()
    except OSError:
        return False
    return bool(proxies.get("https") or proxies.get("all"))


def _sanitize_log_value(value: str, limit: int = 240) -> str:
    message = _CONTROL_PATTERN.sub(" ", str(value))
    message = " ".join(message.split())
    message = _URL_PATTERN.sub("[url]", message)
    message = _CREDENTIAL_PATTERN.sub(r"\1=[redacted]", message)
    return message[:limit]


def _safe_exception_message(error: BaseException) -> str:
    return _sanitize_log_value(str(error))


def _safe_header(headers, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except (AttributeError, TypeError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return _sanitize_log_value(value, limit=160)


def _google_error_fields(response_body: bytes, http_status: int):
    try:
        payload = json.loads(response_body)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("code") != http_status:
        return None, None
    status = error.get("status")
    if not isinstance(status, str) or not _GOOGLE_ERROR_STATUS_PATTERN.fullmatch(status):
        return None, None
    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        message = None
    else:
        message = _sanitize_log_value(message, limit=240)
    return status, message


def _classify_http_origin(server, via, content_type, google_error_status):
    if google_error_status:
        return "google_api_error_format"
    if via or (server and _GATEWAY_SERVER_PATTERN.search(server)):
        return "proxy_or_gateway"
    if content_type and "text/html" in content_type.casefold():
        return "proxy_or_gateway_likely"
    return "unknown"


def _log_http_failure(error: GeminiHTTPError) -> None:
    logger.warning(
        "Gemini HTTP response origin=%s http_status=%s server=%s via=%s "
        "content_type=%s google_error_status=%s google_error_message=%s "
        "https_proxy_configured=%s",
        error.origin,
        error.code,
        error.server,
        error.via,
        error.content_type,
        error.google_error_status,
        error.google_error_message,
        _https_proxy_configured(),
    )


def _log_transport_failure(error: BaseException, *, http_status=None) -> None:
    cause = getattr(error, "reason", None)
    diagnostic_error = cause if isinstance(cause, BaseException) else error
    logger.warning(
        "Gemini transport failure exception_class=%s message=%s "
        "http_status=%s https_proxy_configured=%s",
        type(diagnostic_error).__name__,
        _safe_exception_message(diagnostic_error),
        http_status,
        _https_proxy_configured(),
    )


def generate_content(
    *,
    api_key: str,
    model: str,
    payload: dict,
    timeout: float = 90,
    max_response_bytes: int = 2_000_000,
) -> dict:
    request = Request(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{quote(model, safe='')}:generateContent",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(max_response_bytes + 1)
    except HTTPError as error:
        try:
            response_body = error.read(16_384)
        except OSError:
            response_body = b""
        http_error = GeminiHTTPError(
            error.code, response_body, headers=error.headers
        )
        _log_http_failure(http_error)
        raise http_error from error
    except socket.timeout as error:
        _log_transport_failure(error)
        raise GeminiTransportError("Unable to reach Gemini.") from error
    except TimeoutError as error:
        _log_transport_failure(error)
        raise GeminiTransportError("Unable to reach Gemini.") from error
    except URLError as error:
        _log_transport_failure(error)
        raise GeminiTransportError("Unable to reach Gemini.") from error

    if len(data) > max_response_bytes:
        raise GeminiResponseError("Gemini response exceeded the transport limit.")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise GeminiResponseError("Gemini returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise GeminiResponseError("Gemini returned an invalid response object.")
    return result