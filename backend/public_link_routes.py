import base64
import html
import ipaddress
import logging
import os
import re
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal, Optional
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from groq_transport import groq_http_options
from knowledge_routes import require_admin
import knowledge_store


router = APIRouter()
logger = logging.getLogger(__name__)

MAX_URL_LENGTH = 2_048
MAX_SOURCE_CHARS = 200_000
MAX_DNS_ADDRESSES = 16
CHUNK_SIZE = 6_000
CHUNK_OVERLAP = 600
DIRECT_REVIEW_CHARS = 6_000
MAX_REVIEW_LINE_CHARS = 1_000
MAX_REDIRECTS = 5
REDIRECT_CODES = {300, 301, 302, 303, 307, 308}
INTERNAL_HOST_SUFFIXES = {
    "corp", "home", "home.arpa", "internal", "intranet", "lan", "local",
    "localhost", "localdomain", "onion", "private", "test", "invalid", "example",
}
BOILERPLATE_HEADINGS = {
    "cookie settings", "header", "footer", "navigation", "related articles", "advertisement",
    "related content", "share this page", "subscribe", "table of contents",
}
BOILERPLATE_LINES = re.compile(
    r"(?:skip to (?:main )?content|back to top|cookie settings|"
    r"accept all cookies|reject all cookies|manage cookies|all rights reserved|"
    r"advertisement|advertisements|accept cookies|reject cookies|"
    r"(?:we|this website) use(?:s)? cookies to (?:improve|enhance) your (?:browsing )?experience)[.!]?",
    re.IGNORECASE,
)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
LINK_ONLY = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*\[[^\]]+\]\(.+\)\s*$")
MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)\)")
NAVIGATION_LABELS = {"home", "about", "about us", "contact", "contact us", "sign in", "log in", "menu"}
UNUSABLE_LINE = re.compile(
    r"(?:access denied|(?:error[: ]+)?(?:401 unauthorized|403 forbidden|404 not found|"
    r"500 internal server error)|page not found|just a moment[.!]*|"
    r"checking your browser[.!]*|verify (?:that )?you are human[.!]*|"
    r"please (?:enable javascript|sign in|log in) to (?:continue|view this page)[.!]*|"
    r"you (?:do not|don't) have permission to access (?:this page|this resource)[.!]*)",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")
NUMERIC_HOST_LABEL = re.compile(
    r"(?:[0-9]+|0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+)"
)


class PublicLinkPreviewRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=MAX_URL_LENGTH)
    title: Optional[str] = Field(default=None, max_length=120)


class GenerationMetrics(BaseModel):
    call_count: int = Field(default=0, ge=0, le=1)
    source_chars: int = Field(default=0, ge=0, le=MAX_SOURCE_CHARS)
    submitted_chars: int = Field(default=0, ge=0)
    estimated_input_tokens: Optional[int] = Field(default=None, ge=0)
    input_token_budget: Optional[int] = Field(default=None, ge=0)
    max_completion_tokens: Optional[int] = Field(default=None, ge=0)
    actual_input_tokens: Optional[int] = Field(default=None, ge=0)
    actual_output_tokens: Optional[int] = Field(default=None, ge=0)
    actual_total_tokens: Optional[int] = Field(default=None, ge=0)
    usage_status: Literal["not_requested", "missing", "partial", "available", "invalid"] = "not_requested"
    latency_ms: float = Field(default=0, ge=0)
    outcome: Literal[
        "direct", "normalized", "source_preserved", "structured_source",
        "completion_budget", "input_limit", "failed", "deterministic_formatted", "generated",
    ] = "direct"


class PublicLinkExtractionMetadata(BaseModel):
    provider: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=100)
    retrieval_status: Optional[str] = Field(default=None, max_length=100)
    source_text_available: Optional[bool] = None
    canonical_url: Optional[str] = Field(default=None, max_length=MAX_URL_LENGTH)
    page_title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=10_000)
    author: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[str] = Field(default=None, max_length=100)
    content_chars: Optional[int] = Field(default=None, ge=0, le=MAX_SOURCE_CHARS * 4)
    raw_content_chars: Optional[int] = Field(default=None, ge=0, le=MAX_SOURCE_CHARS * 4)
    useful_content_chars: Optional[int] = Field(default=None, ge=0, le=MAX_SOURCE_CHARS)
    chunk_count: Optional[int] = Field(default=None, ge=0, le=10_000)
    generation_batch_count: Optional[int] = Field(default=None, ge=0, le=1_000)
    generation_metrics: Optional[GenerationMetrics] = None


class PublicLinkSaveRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=MAX_URL_LENGTH)
    title: Optional[str] = Field(default=None, max_length=120)
    text: str = Field(..., min_length=1, max_length=MAX_SOURCE_CHARS)
    metadata: Optional[PublicLinkExtractionMetadata] = None


@dataclass(frozen=True)
class ValidatedPublicURL:
    url: str
    hostname: str
    port: int
    scheme: str
    addresses: tuple[str, ...]


def _is_public_address(value: str) -> bool:
    if "%" in value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False

    def is_global_public(candidate) -> bool:
        if not candidate.is_global or candidate.is_reserved:
            return False
        if (
            isinstance(candidate, ipaddress.IPv4Address)
            and candidate in ipaddress.IPv4Network("192.0.0.0/24")
        ):
            return False
        return True

    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_global_public(address.ipv4_mapped)
        if address.sixtofour is not None:
            return is_global_public(address.sixtofour)
        if address.teredo is not None:
            server, client = address.teredo
            return is_global_public(server) and is_global_public(client)
        nat64_prefix = ipaddress.IPv6Network("64:ff9b::/96")
        if address in nat64_prefix:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            return is_global_public(embedded)
    return is_global_public(address)


def _resolve_hostname_via_proxy(hostname: str) -> tuple[str, ...]:
    options = groq_http_options()
    client = options.get("http_client")
    if client is None:
        raise HTTPException(status_code=422, detail="The public URL hostname could not be resolved.")

    addresses = []
    try:
        for record_type in ("A", "AAAA"):
            # Bootstrap DNS without asking the proxy to resolve the resolver itself;
            # HTTPS still verifies the resolver's certificate against its IP SAN.
            response = client.get(
                "https://1.1.1.1/dns-query",
                params={"name": hostname, "type": record_type},
                headers={"accept": "application/dns-json"},
                timeout=5,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("Status") != 0:
                continue
            expected_type = 1 if record_type == "A" else 28
            for answer in payload.get("Answer", []):
                if answer.get("type") == expected_type:
                    try:
                        addresses.append(str(ipaddress.ip_address(answer["data"])))
                    except (KeyError, ValueError):
                        raise HTTPException(
                            status_code=502,
                            detail="The public URL hostname could not be safely verified through the configured proxy.",
                        ) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="The public URL hostname could not be verified through the configured proxy.",
        ) from None
    finally:
        client.close()

    unique_addresses = tuple(dict.fromkeys(addresses))
    if not unique_addresses:
        raise HTTPException(status_code=422, detail="The public URL hostname could not be resolved.")
    return unique_addresses


def _read_probe_response(response) -> tuple[int, Optional[str]]:
    for _ in range(5):
        status_line = response.readline(8_193)
        if len(status_line) > 8_192 or not status_line.startswith(b"HTTP/"):
            raise OSError("Invalid HTTP response.")
        status_parts = status_line.decode("latin-1").split(" ", 2)
        if len(status_parts) < 2 or not status_parts[1].isdigit():
            raise OSError("Invalid HTTP status.")
        status = int(status_parts[1])
        location = None
        header_bytes = len(status_line)
        headers_complete = False
        for _ in range(100):
            line = response.readline(8_193)
            header_bytes += len(line)
            if header_bytes > 32_768 or len(line) > 8_192 or not line:
                raise OSError("Invalid or oversized HTTP response headers.")
            if line in {b"\r\n", b"\n"}:
                headers_complete = True
                break
            name, separator, value = line.decode("latin-1").partition(":")
            if separator and name.lower() == "location":
                location = value.strip()
        if not headers_complete:
            raise OSError("HTTP response headers are incomplete.")
        if 100 <= status < 200 and status != 101:
            continue
        if status == 101:
            raise OSError("Protocol upgrades are not supported.")
        return status, location
    raise OSError("Too many informational HTTP responses.")


def _proxy_settings():
    proxy_url = os.getenv("GROQ_PROXY_URL", "").strip()
    if not proxy_url:
        return None
    try:
        parsed = urlsplit(proxy_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        username = unquote(parsed.username) if parsed.username is not None else None
        password = unquote(parsed.password or "")
        if any(ord(char) < 32 or ord(char) == 127 for char in (username or "") + password):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=503, detail="GROQ_PROXY_URL must be a valid HTTP(S) proxy URL.") from None

    authorization = None
    if username is not None:
        credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        authorization = f"Proxy-Authorization: Basic {credentials}\r\n"
    return parsed, port, authorization


def _public_link_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    ca_bundle = os.getenv("PUBLIC_LINK_CA_BUNDLE", "").strip()
    if ca_bundle:
        try:
            if not os.path.isabs(ca_bundle):
                raise ValueError("An absolute path is required.")
            context.load_verify_locations(cafile=ca_bundle)
        except (OSError, ValueError):
            logger.warning("Public Link TLS configuration failure: category=invalid_ca_bundle")
            raise HTTPException(
                status_code=503,
                detail=(
                    "PUBLIC_LINK_CA_BUNDLE must point to a readable, valid PEM CA bundle "
                    "approved by your organization. Configure it and restart the backend."
                ),
            ) from None
    return context


def _log_probe_error(error: OSError, route: str) -> None:
    category = (
        "certificate_verification" if isinstance(error, ssl.SSLCertVerificationError)
        else "timeout" if isinstance(error, TimeoutError)
        else "tls" if isinstance(error, ssl.SSLError)
        else "connection_or_http"
    )
    verify_code = getattr(error, "verify_code", None)
    logger.warning(
        "Public Link probe failure: route=%s category=%s tls_verify_code=%s",
        route, category, verify_code if type(verify_code) is int else None,
    )


def _probe_failure(error: Optional[OSError]) -> HTTPException:
    if isinstance(error, ssl.SSLCertVerificationError):
        detail = (
            "Unable to verify the public page TLS certificate. Check the certificate chain "
            "and hostname; if an approved inspection proxy is used, configure "
            "PUBLIC_LINK_CA_BUNDLE with your organization's approved CA bundle. "
            "Certificate verification remains required."
        )
    elif isinstance(error, TimeoutError):
        detail = "Timed out while verifying the public page location. Check backend network/proxy connectivity."
    else:
        detail = "Unable to verify the public page location."
    return HTTPException(status_code=502, detail=detail)


def _send_redirect_probe_via_proxy(target: ValidatedPublicURL) -> Optional[tuple[int, Optional[str]]]:
    settings = _proxy_settings()
    if settings is None:
        return None
    proxy, proxy_port, proxy_authorization = settings
    parts = urlsplit(target.url)
    request_target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        request_target += "?" + quote(parts.query, safe="/?@:!$&'()*+,;=-._~")
    host_header = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
    last_error = None

    for address in target.addresses:
        connection = None
        response = None
        try:
            connection = socket.create_connection((proxy.hostname, proxy_port), timeout=5)
            if proxy.scheme == "https":
                connection = _public_link_tls_context().wrap_socket(
                    connection, server_hostname=proxy.hostname
                )

            if target.scheme == "https":
                connect_host = f"[{address}]" if ":" in address else address
                connect_authority = f"{connect_host}:{target.port}"
                connect_request = (
                    f"CONNECT {connect_authority} HTTP/1.1\r\n"
                    f"Host: {connect_authority}\r\n"
                    f"{proxy_authorization or ''}"
                    "Connection: keep-alive\r\n\r\n"
                ).encode("ascii")
                connection.sendall(connect_request)
                response = connection.makefile("rb")
                status, _ = _read_probe_response(response)
                response.close()
                response = None
                if status != 200:
                    raise OSError("The proxy could not establish a verified tunnel.")
                connection = _public_link_tls_context().wrap_socket(
                    connection, server_hostname=target.hostname
                )
                request_data = (
                    f"GET {request_target} HTTP/1.1\r\n"
                    f"Host: {host_header}\r\n"
                    "User-Agent: HelixPublicLinkPreview/1.0\r\n"
                    "Range: bytes=0-0\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            else:
                connect_host = f"[{address}]" if ":" in address else address
                authority = f"{connect_host}:{target.port}"
                proxy_target = urlunsplit(
                    (target.scheme, authority, parts.path or "/", parts.query, "")
                )
                request_data = (
                    f"GET {proxy_target} HTTP/1.1\r\n"
                    f"Host: {host_header}\r\n"
                    "User-Agent: HelixPublicLinkPreview/1.0\r\n"
                    "Range: bytes=0-0\r\n"
                    "Connection: close\r\n"
                    f"{proxy_authorization or ''}\r\n"
                ).encode("ascii")

            connection.sendall(request_data)
            response = connection.makefile("rb")
            return _read_probe_response(response)
        except OSError as error:
            _log_probe_error(error, "proxy")
            if not isinstance(last_error, ssl.SSLCertVerificationError):
                last_error = error
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    raise _probe_failure(last_error) from None


def validate_public_url(value: str) -> ValidatedPublicURL:
    raw_url = value.strip()
    if (
        not raw_url
        or len(raw_url) > MAX_URL_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in raw_url)
    ):
        raise HTTPException(status_code=422, detail="Enter a valid public HTTP or HTTPS URL.")

    try:
        parts = urlsplit(raw_url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise HTTPException(status_code=422, detail="Enter a valid public HTTP or HTTPS URL.") from None

    if (
        scheme not in {"http", "https"}
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or "%" in hostname
    ):
        raise HTTPException(status_code=422, detail="Enter a valid public HTTP or HTTPS URL.")

    default_port = 80 if scheme == "http" else 443
    port = port or default_port
    if port != default_port:
        raise HTTPException(status_code=422, detail="Only standard public HTTP and HTTPS ports are allowed.")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise HTTPException(status_code=422, detail="The public URL hostname is invalid.") from None

        labels = hostname.split(".")
        if (
            len(hostname) > 253
            or len(labels) < 2
            or any(not label or len(label) > 63 for label in labels)
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels)
            or any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in INTERNAL_HOST_SUFFIXES)
            or all(NUMERIC_HOST_LABEL.fullmatch(label) for label in labels)
        ):
            raise HTTPException(status_code=422, detail="Internal or invalid hostnames are not allowed.")

        try:
            resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError:
            addresses = _resolve_hostname_via_proxy(hostname)
        else:
            addresses = tuple(dict.fromkeys(item[4][0] for item in resolved))
        if not addresses:
            raise HTTPException(status_code=422, detail="The public URL hostname could not be resolved.")
        if len(addresses) > MAX_DNS_ADDRESSES:
            raise HTTPException(status_code=422, detail="The public URL hostname has too many addresses.")
    else:
        hostname = address.compressed
        addresses = (hostname,)

    if any(not _is_public_address(resolved_address) for resolved_address in addresses):
        raise HTTPException(status_code=422, detail="URLs resolving to non-public IP addresses are not allowed.")

    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    normalized_url = urlunsplit(
        (scheme, host_for_url, parts.path or "/", parts.query, "")
    )
    return ValidatedPublicURL(normalized_url, hostname, port, scheme, addresses)


def _send_redirect_probe(target: ValidatedPublicURL) -> tuple[int, Optional[str]]:
    parts = urlsplit(target.url)
    request_target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        request_target += "?" + quote(parts.query, safe="/?@:!$&'()*+,;=-._~")
    host_header = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
    request_data = (
        f"GET {request_target} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "User-Agent: HelixPublicLinkPreview/1.0\r\n"
        "Range: bytes=0-0\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    last_error = None

    for address in target.addresses:
        connection = None
        response = None
        try:
            connection = socket.create_connection((address, target.port), timeout=5)
            if target.scheme == "https":
                context = _public_link_tls_context()
                connection = context.wrap_socket(connection, server_hostname=target.hostname)
            connection.sendall(request_data)
            response = connection.makefile("rb")
            return _read_probe_response(response)
        except OSError as error:
            _log_probe_error(error, "direct")
            if not isinstance(last_error, ssl.SSLCertVerificationError):
                last_error = error
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    if last_error is not None:
        proxy_result = _send_redirect_probe_via_proxy(target)
        if proxy_result is not None:
            return proxy_result
        raise _probe_failure(last_error) from None
    raise _probe_failure(last_error)


def resolve_public_redirects(value: str) -> str:
    current_url = value.strip()
    for redirect_number in range(MAX_REDIRECTS + 1):
        target = validate_public_url(current_url)
        status, location = _send_redirect_probe(target)
        if status in REDIRECT_CODES:
            if not location:
                raise HTTPException(status_code=422, detail="The public page returned an invalid redirect.")
            if redirect_number == MAX_REDIRECTS:
                raise HTTPException(status_code=422, detail="The public page has too many redirects.")
            current_url = urljoin(target.url, location)
            continue
        if status >= 400:
            raise HTTPException(status_code=422, detail="The public page could not be reached.")
        return target.url
    raise HTTPException(status_code=422, detail="The public page has too many redirects.")


def _protect_markdown_code(content: str) -> tuple[str, dict[str, str]]:
    prefix = "HELIX_CODE_"
    while prefix in content:
        prefix += "_"
    protected = {}

    def protect(value):
        token = f"{prefix}{len(protected)}_END"
        protected[token] = value
        return token

    lines = content.split("\n")
    output = []
    index = 0
    while index < len(lines):
        line = lines[index]
        fence_line = re.sub(r"^(?: {0,3}> ?)+", "", line)
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", fence_line)
        if fence or line.startswith(("    ", "\t")):
            start = index
            index += 1
            if fence:
                marker = fence.group(1)
                closing = re.compile(rf"^ {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}\s*$")
                while index < len(lines):
                    index += 1
                    if closing.fullmatch(re.sub(r"^(?: {0,3}> ?)+", "", lines[index - 1])):
                        break
            else:
                while index < len(lines) and (
                    not lines[index].strip() or lines[index].startswith(("    ", "\t"))
                ):
                    index += 1
            output.append(protect("\n".join(lines[start:index])))
        else:
            output.append(line)
            index += 1
    content = "\n".join(output)
    content = re.sub(
        r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)",
        lambda match: protect(match.group(0)), content, flags=re.DOTALL,
    )
    return content, protected


def _is_boilerplate_line(line: str) -> bool:
    if BOILERPLATE_LINES.fullmatch(line):
        return True
    linked_control = re.fullmatch(r"\[([^\]]+)\]\([^)]+\)", line)
    return bool(linked_control and (
        BOILERPLATE_LINES.fullmatch(linked_control.group(1))
        or linked_control.group(1).casefold() in {"privacy policy", "terms of use"}
    ))


def _link_row(line: str) -> list[tuple[str, str]]:
    if line.lstrip().startswith(">"):
        return []
    line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
    if line.startswith("|"):
        return []
    links = MARKDOWN_LINK.findall(line)
    remainder = MARKDOWN_LINK.sub("", line)
    return links if links and not remainder.strip(" \t|>/\u00b7\u2022\u203a\u00bb") else []


def _heading_key(value: str) -> str:
    return re.sub(r"[^\w\s-]", "", value.casefold()).strip()


def _remove_contact_details(content: str) -> str:
    parts = re.split(r"(https?://[^\s<>]+)", content)
    return "".join(
        part if index % 2 else _redact_contact_text(part)
        for index, part in enumerate(parts)
    )


def _redact_contact_text(content: str) -> str:
    phone = re.compile(
        r"(?<![\w/])(?:\+\d{1,3}[ .()-]*\d(?:[ .()-]*\d){6,13}|"
        r"\(\d{3}\)[ .-]*\d{3}[ .-]\d{4}|\d{3}[-.]\d{3}[-.]\d{4})(?![\w/])"
    )
    content = phone.sub("[phone removed]", content)
    content = re.sub(
        r"(?im)(\b(?:phone|telephone|tel|mobile|fax)\s*:\s*)"
        r"\d(?:[ ()-]*\d){6,14}(?!\w)",
        r"\1[phone removed]", content,
    )
    content = re.sub(r"(?i)\[([^\]\n]*)\]\(tel:[^)\n]*\)", "[phone removed]", content)
    content = re.sub(
        r"(?im)^[ \t]*(?:postal|mailing|street|office)[ \t]+address[ \t]*:"
        r"[^\n]+", "[address removed]", content,
    )
    content = re.sub(
        r"(?i)(?<![\w/])\d{1,6}\s+(?:[\w.'-]+\s+){1,5}"
        r"(?:street|road|avenue|boulevard|lane|drive|strasse|straße)"
        r"\b",
        "[address removed]", content,
    )
    return re.sub(
        r"(?im)^[ \t]*(?:(?:phone|telephone|tel|mobile|fax)\s*:\s*)?"
        r"\[(?:phone|address) removed\][ \t]*$", "", content,
    )


def _remove_minified_assets(content: str) -> str:
    lines = content.split("\n")
    asset_marker = re.compile(
        r"(?:sourceMappingURL\s*=\s*\S+\.map|"
        r"(?:window|self)\s*\.\s*webpackChunk|__webpack_require__)"
    )
    for index, line in enumerate(lines):
        if (
            len(line) > MAX_REVIEW_LINE_CHARS and asset_marker.search(line)
            and line.count(";") + line.count("{") >= 20
            and len(re.findall(r"\s", line)) < len(line) // 10
        ):
            lines[index] = ""
    return "\n".join(lines)


def _remove_html_controls(content: str) -> str:
    class ChromeParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.offsets = [0]
            for match in re.finditer("\n", content):
                self.offsets.append(match.end())
            self.tag = None
            self.depth = 0
            self.code_depth = 0
            self.start = 0
            self.spans = []

        def position(self):
            row, column = self.getpos()
            return self.offsets[row - 1] + column

        def handle_starttag(self, tag, attrs):
            if self.tag:
                if tag == self.tag:
                    self.depth += 1
                return
            if tag in {"pre", "code"}:
                self.code_depth += 1
            if self.code_depth:
                return
            role = dict(attrs).get("role") or ""
            if tag in {"input", "button", "textarea", "select", "form", "address"} or role.casefold() in {
                "navigation", "banner", "contentinfo", "form",
            }:
                if tag == "input":
                    start = self.position()
                    self.spans.append((start, start + len(self.get_starttag_text())))
                    return
                self.tag, self.depth, self.start = tag, 1, self.position()

        def handle_startendtag(self, tag, attrs):
            if (
                not self.tag and not self.code_depth
                and tag in {"input", "button", "textarea", "select", "form", "address"}
            ):
                start = self.position()
                self.spans.append((start, start + len(self.get_starttag_text())))

        def handle_endtag(self, tag):
            if self.code_depth:
                if tag in {"pre", "code"}:
                    self.code_depth -= 1
                return
            if tag != self.tag:
                return
            self.depth -= 1
            if not self.depth:
                self.spans.append((self.start, content.index(">", self.position()) + 1))
                self.tag = None

    parser = ChromeParser()
    parser.feed(content)
    for start, end in reversed(parser.spans):
        content = content[:start] + "\n\n" + content[end:]
    return content


def _remove_markdown_chrome(content: str) -> str:
    lines = content.split("\n")
    headings = {}
    first_heading = len(lines)
    for index, line in enumerate(lines):
        heading = HEADING.match(line)
        if heading:
            first_heading = min(first_heading, index)
            key = _heading_key(heading.group(2))
            slug = re.sub(r"\s+", "-", key)
            headings.setdefault(slug, []).append((index, key))

    runs = []
    index = 0
    while index < len(lines):
        if not _link_row(lines[index]):
            index += 1
            continue
        start = index
        links = []
        while index < len(lines) and _link_row(lines[index]):
            links.extend(_link_row(lines[index]))
            index += 1
        ordered = any(re.match(r"^\s*\d+[.)]\s+", line) for line in lines[start:index])
        runs.append((start, index, links, ordered))

    run_counts = {}
    for _, _, links, ordered in runs:
        if not ordered:
            signature = tuple(links)
            run_counts[signature] = run_counts.get(signature, 0) + 1
    navigation = set()
    for start, _, links, ordered in runs:
        # Require a compact menu row or repeated UI, not just a local-link list.
        menu_shape = len(_link_row(lines[start])) >= 3 or run_counts.get(tuple(links), 0) >= 2
        if (
            not ordered and menu_shape and start < first_heading and len(links) >= 3
            and any(target == "/" for _, target in links)
            and all(target.startswith("/") and not target.startswith("//") for _, target in links)
        ):
            navigation.add(tuple(links))

    for start, end, links, ordered in runs:
        positions = [
            next((
                position for position, key in headings.get(unquote(target[1:]).casefold(), [])
                if position >= end and _heading_key(label) == key
            ), -1) if target.startswith("#") else -1
            for label, target in links
        ]
        outline = (
            not ordered and len(links) >= 2
            and all(position >= 0 for position in positions)
            and all(left < right for left, right in zip(positions, positions[1:]))
        )
        if (not ordered and tuple(links) in navigation) or outline:
            lines[start:end] = [""] * (end - start)
    return "\n".join(lines)


def clean_markdown(markdown: str) -> str:
    content, protected = _protect_markdown_code(markdown.replace("\r\n", "\n").replace("\r", "\n"))
    content = _remove_minified_assets(content)
    for token, value in protected.items():
        protected[token] = _remove_minified_assets(value)
    content = re.sub(r"(?is)<!--.*?-->", "", content)
    content = re.sub(
        r"(?is)<(script|style|noscript|nav|footer)\b[^>]*>.*?</\1\s*>", "", content
    )
    content = _remove_html_controls(content)
    content = re.sub(
        r"(?is)<h([1-6])\b[^>]*>(.*?)</h\1\s*>",
        lambda match: "\n\n" + "#" * int(match.group(1)) + " " + match.group(2) + "\n\n",
        content,
    )
    content = re.sub(r"(?i)<br\s*/?>|</(?:p|div)\s*>", "\n\n", content)
    content = re.sub(r"(?i)</?(?:html|body|main|article|p|div|span|strong|em|b|i)\b[^>]*>", "", content)
    content = html.unescape(content)
    content = _remove_markdown_chrome(content)

    sections = []
    current_lines = []
    seen_sections = set()

    def append_section():
        section = "\n".join(current_lines).strip()
        if not section:
            return
        heading = HEADING.match(current_lines[0])
        body = [line for line in current_lines[1:] if line.strip()] if heading else current_lines
        # A heading alone is not evidence that a substantive section is boilerplate.
        if (
            heading and heading.group(2).casefold() in BOILERPLATE_HEADINGS
            and not any(re.match(r"^\s*\d+[.)]\s+", line) for line in body)
            and all(_link_row(line) or _is_boilerplate_line(line) for line in body)
        ):
            return
        restored = section
        for token, value in protected.items():
            restored = restored.replace(token, value)
        if restored in seen_sections:
            return
        seen_sections.add(restored)
        blocks = []
        seen_paragraphs = set()
        for block in section.split("\n\n"):
            block_lines = block.splitlines()
            if len(block_lines) >= 2 and all(
                LINK_ONLY.fullmatch(line)
                and re.search(r"\[([^\]]+)\]", line).group(1).casefold() in NAVIGATION_LABELS
                for line in block_lines
            ):
                continue
            # Do not deduplicate steps, table rows, code, or context-dependent list items.
            prose = (
                len(block) >= 80
                and not any(token in block for token in protected)
                and not re.search(r"(?m)^\s*(?:[#>|*+-]|\d+[.)])", block)
                and "|" not in block
            )
            if prose and block in seen_paragraphs:
                continue
            if prose:
                seen_paragraphs.add(block)
            blocks.append(block)
        sections.append("\n\n".join(blocks))

    for raw_line in content.split("\n"):
        line = raw_line.rstrip()
        if raw_line.endswith("  ") and line:
            line += "  "
        if _is_boilerplate_line(line.strip()):
            continue
        match = HEADING.match(line)
        if match:
            append_section()
            current_lines.clear()
        if line.strip() or (current_lines and current_lines[-1]):
            current_lines.append(line)
    append_section()
    cleaned = "\n\n".join(sections).strip()
    for token, value in protected.items():
        cleaned = cleaned.replace(token, value)
    return _remove_contact_details(cleaned).strip()


def validate_extracted_content(markdown: str, cleaned: str) -> int:
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    body = [
        line for line in lines
        if not HEADING.match(line) and not re.fullmatch(r"(?:`{3,}|~{3,}).*|[-*_=\s|:]+", line)
    ]
    useful_chars = sum(len(re.sub(r"\s+", "", line)) for line in body)
    reason = None
    has_code = any(re.match(r"^(`{3,}|~{3,})", line) for line in lines)
    if not body or not (has_code or any(char.isalnum() for line in body for char in line)):
        reason = "no_useful_content"
    elif all(UNUSABLE_LINE.fullmatch(re.sub(r"^#{1,6}\s+", "", line)) for line in lines):
        reason = "blocked_page"
    elif len(cleaned) < 200 and len(cleaned) < len(markdown) * 0.05:
        reason = "excessive_cleaning"
    if reason:
        logger.warning(
            "Public Link extraction rejected: category=%s raw_chars=%s cleaned_chars=%s useful_chars=%s",
            reason, len(markdown), len(cleaned), useful_chars,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                "The extraction has no usable page content, is an access/error page, or lost too much "
                "content during cleaning. Use a direct public content URL or Paste Text to review "
                "the source manually; no knowledge was generated."
            ),
        )
    return useful_chars


def split_content_chunks(
    content: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size and overlap are invalid.")

    chunks = []
    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if end < len(content):
            boundary = max(
                content.rfind("\n\n", start + chunk_size // 2, end),
                content.rfind("\n", start + chunk_size // 2, end),
                content.rfind(" ", start + chunk_size // 2, end),
            )
            if boundary > start:
                end = boundary
        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(content):
            break
        start = max(end - overlap, start + 1)
        while start < len(content) and content[start].isspace():
            start += 1
    return chunks


def needs_normalization(content: str) -> bool:
    return len(content) > DIRECT_REVIEW_CHARS and any(
        len(line) > MAX_REVIEW_LINE_CHARS for line in content.splitlines()
    )


def _is_structured_content(content: str) -> bool:
    return (
        re.search(r"(?m)^[ \t]|^(?:[-+*>]|\d+[.)]\s)| {2,}|\t|_", content) is not None
        or re.fullmatch(r"[\w\s.,;:!?()'\"\-\u2019\u2013\u2014]+", content) is None
        or re.search(r"\bwww\.", content, re.IGNORECASE) is not None
    )


def _wrap_prose_line(line: str) -> str:
    parts = []
    start = 0
    while len(line) - start > MAX_REVIEW_LINE_CHARS:
        boundary = line.rfind(" ", start, start + MAX_REVIEW_LINE_CHARS + 1)
        if boundary <= start:
            break
        parts.append(line[start:boundary])
        start = boundary + 1
    parts.append(line[start:])
    return "\n".join(parts)


def format_content(content: str, metrics: GenerationMetrics) -> str:
    metrics.source_chars = len(content)
    if not needs_normalization(content):
        return content
    if _is_structured_content(content):
        metrics.outcome = "structured_source"
        return content

    formatted = "\n".join(_wrap_prose_line(line) for line in content.split("\n"))
    # Soft-wrap only at existing spaces; new Markdown structures must not be introduced.
    if _is_structured_content(formatted):
        metrics.outcome = "source_preserved"
        return content
    if (
        WHITESPACE.sub(" ", formatted) != WHITESPACE.sub(" ", content)
        or len(formatted) != len(content)
    ):
        logger.error("Public Link formatting failed source-fidelity validation.")
        raise HTTPException(
            status_code=500,
            detail="Source-preserving formatting failed. No partial preview was returned.",
        )
    metrics.outcome = "deterministic_formatted" if formatted != content else "source_preserved"
    return formatted


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _scrape_result_parts(result):
    markdown = _field(result, "markdown")
    metadata = _field(result, "metadata", {})
    if markdown is None:
        data = _field(result, "data")
        if data is not None:
            markdown = _field(data, "markdown")
            metadata = _field(data, "metadata", metadata)
    return markdown, metadata


def _public_metadata_url(value) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return validate_public_url(value).url
    except HTTPException:
        return None


def _create_firecrawl_client(api_key: str):
    try:
        from firecrawl import Firecrawl
    except ImportError:
        raise HTTPException(
            status_code=503, detail="Public Link scraping support is not installed."
        ) from None
    return Firecrawl(api_key=api_key, max_retries=0)


def _log_generation_metrics(metrics: GenerationMetrics) -> None:
    logger.info("Public Link generation metrics: %s", metrics.model_dump())


def _metadata_value(metadata, *keys):
    for key in keys:
        value = _field(metadata, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@router.post("/api/knowledge/public-link/preview")
def preview_public_link(request: PublicLinkPreviewRequest, _user=Depends(require_admin)):
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not firecrawl_key:
        raise HTTPException(status_code=503, detail="Public Link scraping is not configured.")

    source_url = resolve_public_redirects(request.url)
    source_url = validate_public_url(source_url).url
    try:
        firecrawl = _create_firecrawl_client(firecrawl_key)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Public Link scraping is unavailable.") from None
    try:
        scraped = firecrawl.v1.scrape_url(
            source_url, formats=["markdown"], only_main_content=True
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Unable to scrape the public page.") from None

    if not _field(scraped, "success", True) or _field(scraped, "error"):
        raise HTTPException(status_code=502, detail="Unable to scrape the public page.")
    scraped_url = _field(scraped, "url")
    if scraped_url:
        verified_scraped_url = _public_metadata_url(scraped_url)
        if not verified_scraped_url:
            raise HTTPException(status_code=502, detail="The scraper returned a non-public page URL.")
        source_url = verified_scraped_url
    markdown, scraped_metadata = _scrape_result_parts(scraped)
    extracted_status = _field(scraped_metadata, "statusCode")
    if isinstance(extracted_status, int) and extracted_status >= 400:
        logger.warning("Public Link extraction rejected: category=page_http_error status=%s", extracted_status)
        raise HTTPException(
            status_code=422,
            detail="The scraper received an error page. Use a direct, publicly accessible content URL.",
        )
    if not isinstance(markdown, str) or not markdown.strip():
        logger.warning("Public Link extraction rejected: category=empty_markdown")
        raise HTTPException(
            status_code=422,
            detail="The scraper returned no readable Markdown. Use a direct public content URL or Paste Text.",
        )
    if len(markdown) > MAX_SOURCE_CHARS * 4:
        raise HTTPException(
            status_code=413,
            detail="The public page exceeds the preview content limit. Use a more specific page URL.",
        )
    cleaned_content = clean_markdown(markdown)
    if len(cleaned_content) > MAX_SOURCE_CHARS:
        raise HTTPException(
            status_code=413,
            detail="The public page exceeds the preview content limit. Use a more specific page URL.",
        )
    useful_content_chars = validate_extracted_content(markdown, cleaned_content)
    logger.info(
        "Public Link extraction: raw_chars=%s cleaned_chars=%s useful_chars=%s",
        len(markdown), len(cleaned_content), useful_content_chars,
    )

    chunks = split_content_chunks(cleaned_content)
    page_title = (
        _field(scraped, "title")
        or _metadata_value(scraped_metadata, "title", "ogTitle")
    )
    page_title = page_title.strip()[:500] if isinstance(page_title, str) else None
    title = (request.title or "").strip() or (page_title or source_url)
    title = title[:120]
    generation_metrics = GenerationMetrics(source_chars=len(cleaned_content))
    generated_content = format_content(cleaned_content, generation_metrics)
    _log_generation_metrics(generation_metrics)

    canonical_candidate = _metadata_value(
        scraped_metadata, "canonical_url", "canonicalURL", "canonical", "og:url"
    )
    canonical_url = _public_metadata_url(canonical_candidate)
    useful_metadata = {
        "source_url": source_url,
        "canonical_url": canonical_url,
        "page_title": page_title,
        "description": _field(scraped, "description")
        or _metadata_value(scraped_metadata, "description", "ogDescription"),
        "author": _metadata_value(scraped_metadata, "author"),
        "published_at": _metadata_value(
            scraped_metadata, "publishedTime", "published_time", "article:published_time"
        ),
        "content_chars": len(cleaned_content),
        "raw_content_chars": len(markdown),
        "useful_content_chars": useful_content_chars,
        "chunk_count": len(chunks),
        "generation_batch_count": generation_metrics.call_count,
        "generation_metrics": generation_metrics.model_dump(),
    }
    return {
        "success": True,
        "title": title,
        "url": source_url,
        "source_url": source_url,
        "generated_content": generated_content,
        "cleaned_content": cleaned_content,
        "metadata": useful_metadata,
    }


@router.post("/api/knowledge/public-link")
def save_public_link(request: PublicLinkSaveRequest, _user=Depends(require_admin)):
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="Reviewed content must not be blank.")

    source_url = validate_public_url(request.url).url
    title = (request.title or "").strip()
    extraction_metadata = (
        request.metadata.model_dump(exclude_none=True) if request.metadata else {}
    )
    canonical_url = extraction_metadata.get("canonical_url")
    if canonical_url:
        extraction_metadata["canonical_url"] = validate_public_url(canonical_url).url
    source_metadata = {
        "source_url": source_url,
        **extraction_metadata,
    }

    try:
        source = knowledge_store.save_text(
            title or extraction_metadata.get("page_title") or source_url,
            request.text,
            source_type="Public Link",
            metadata=source_metadata,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"success": True, "source": source}
