import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from document_extractor import (
    DocumentExtractionError,
    SUPPORTED_FORMATS,
    extract_document,
)
import knowledge_store


router = APIRouter()
TOKEN_LIFETIME_SECONDS = 8 * 60 * 60
MAX_DOCUMENT_FILE_BYTES = 50 * 1024 * 1024
MAX_DOCUMENT_PREVIEW_RESPONSE_BYTES = 16 * 1024 * 1024
DOCUMENT_ERROR_RESPONSE_RESERVE_BYTES = 8192
DOCUMENT_READ_BLOCK_BYTES = 1024 * 1024
MAX_DOCUMENT_SAVE_TEXT_CHARS = 200_000
MAX_DOCUMENT_SAVE_BATCH_SIZE = 20
logger = logging.getLogger(__name__)


class TextSubmission(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    text: str = Field(..., min_length=1, max_length=200_000)
    notes: str = Field(default="", max_length=10_000)


class InterviewQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2_000)


class DocumentSaveBatch(BaseModel):
    documents: list[object] = Field(
        ..., min_length=1, max_length=MAX_DOCUMENT_SAVE_BATCH_SIZE
    )


def _session_secret():
    secret = os.getenv("SESSION_SECRET")
    if not secret or len(secret) < 32:
        raise RuntimeError("Session authentication is not configured.")
    return secret.encode("utf-8")


def create_access_token(email, role):
    now = int(time.time())
    payload = {
        "sub": email.strip().lower(),
        "role": role,
        "iat": now,
        "exp": now + TOKEN_LIFETIME_SECONDS,
    }
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        _session_secret(), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def _decode_token(token):
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        expected_signature = hmac.new(
            _session_secret(), encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        provided_signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        if not hmac.compare_digest(expected_signature, provided_signature):
            raise ValueError("Invalid signature.")

        payload_bytes = base64.urlsafe_b64decode(
            encoded_payload + "=" * (-len(encoded_payload) % 4)
        )
        payload = json.loads(payload_bytes.decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("exp"), int)
            or payload["exp"] <= int(time.time())
            or payload.get("role") not in {"Admin", "Employee"}
            or not isinstance(payload.get("sub"), str)
            or not payload["sub"]
        ):
            raise ValueError("Invalid token claims.")
        return payload
    except RuntimeError as error:
        raise HTTPException(
            status_code=500, detail="Session authentication is not configured."
        ) from error
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as error:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def _role_dependency(required_role):
    def authorize(authorization: Optional[str] = Header(default=None)):
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail="A bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(
                status_code=401,
                detail="A valid bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = _decode_token(token.strip())
        if payload["role"] != required_role:
            raise HTTPException(status_code=403, detail="Insufficient role.")
        return payload

    return authorize


require_admin = _role_dependency("Admin")
require_employee = _role_dependency("Employee")


def _document_filename(filename):
    if (
        not filename
        or len(filename) > 255
        or filename.endswith((" ", "."))
        or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in filename)
    ):
        raise DocumentExtractionError("Invalid document filename.")

    stem, separator, extension = filename.rpartition(".")
    if not separator or not stem or f".{extension.lower()}" not in SUPPORTED_FORMATS:
        raise DocumentExtractionError(
            "Unsupported document extension. Use PDF, DOCX, DOC, XLSX, XLS, "
            "PPTX, PPT, or TXT."
        )

    reserved_names = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}"
        for prefix in ("COM", "LPT")
        for number in range(1, 10)
    }
    if stem.split(".", 1)[0].upper() in reserved_names:
        raise DocumentExtractionError("Invalid document filename.")
    return f".{extension.lower()}"


def _document_result(filename, status, *, text=None, error=None, metadata=None):
    result = {
        "filename": filename,
        "status": status,
        "text": text,
        "metadata": metadata or {},
    }
    if error is not None:
        result["error"] = " ".join(error.split())[:512]
    return result


def _serialized_preview_size(results):
    return len(
        json.dumps(
            {"results": results},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


@router.post("/api/knowledge/documents/preview")
async def preview_documents(
    files: list[UploadFile] = File(...),
    _user=Depends(require_admin),
):
    results = []
    for index, upload in enumerate(files):
        display_filename = (upload.filename or "")[:255]
        extension = ""
        size = 0
        try:
            extension = _document_filename(upload.filename or "")
            with tempfile.TemporaryDirectory(prefix="helix_document_upload_") as directory:
                path = Path(directory) / f"document{extension}"
                with path.open("wb") as target:
                    while block := await upload.read(DOCUMENT_READ_BLOCK_BYTES):
                        size += len(block)
                        if size > MAX_DOCUMENT_FILE_BYTES:
                            raise DocumentExtractionError(
                                "Document exceeds the 50 MiB per-file upload limit."
                            )
                        target.write(block)

                if size == 0:
                    raise DocumentExtractionError("The uploaded document is empty.")

                text = await run_in_threadpool(extract_document, path)
                if not isinstance(text, str) or not text.strip():
                    raise DocumentExtractionError(
                        "No readable text or supported content was found."
                    )

                metadata = {
                    "format": extension[1:],
                    "size_bytes": size,
                    "text_characters": len(text),
                    "text_utf8_bytes": len(text.encode("utf-8")),
                }
                result = _document_result(
                    display_filename, "success", text=text, metadata=metadata
                )
                response_size = _serialized_preview_size(results + [result])
                reserved_for_errors = (
                    len(files) - index - 1
                ) * DOCUMENT_ERROR_RESPONSE_RESERVE_BYTES
                if (
                    response_size + reserved_for_errors
                    > MAX_DOCUMENT_PREVIEW_RESPONSE_BYTES
                ):
                    result = _document_result(
                        display_filename,
                        "error",
                        error=(
                            "Extracted text was not returned because the "
                            "preview response would exceed its 16 MiB limit. "
                            "Upload fewer or smaller documents."
                        ),
                        metadata={
                            "format": extension[1:],
                            "size_bytes": size,
                        },
                    )
        except DocumentExtractionError as error:
            result = _document_result(
                display_filename,
                "error",
                error=str(error),
                metadata={"format": extension[1:] if extension else None, "size_bytes": size},
            )
        except OSError:
            logger.exception("Document upload or preview file handling failed")
            result = _document_result(
                display_filename,
                "error",
                error="Unable to read or prepare this document for extraction.",
                metadata={"format": extension[1:] if extension else None, "size_bytes": size},
            )
        except Exception:
            logger.exception("Unexpected document extraction preview failure")
            result = _document_result(
                display_filename,
                "error",
                error="Document extraction failed unexpectedly. Please retry.",
                metadata={"format": extension[1:] if extension else None, "size_bytes": size},
            )
        finally:
            await upload.close()
        results.append(result)

    return {"results": results}


@router.post("/api/knowledge/documents/save")
def save_documents(request: DocumentSaveBatch, _user=Depends(require_admin)):
    results = []
    for index, item in enumerate(request.documents):
        filename = (
            item.get("filename", "")[:255]
            if isinstance(item, dict) and isinstance(item.get("filename"), str)
            else f"Document {index + 1}"
        )
        try:
            if not isinstance(item, dict):
                raise ValueError("Each document must be an object.")
            requested_filename = item.get("filename")
            if not isinstance(requested_filename, str):
                raise DocumentExtractionError("Invalid document filename.")
            extension = _document_filename(requested_filename)
            filename = requested_filename

            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Extracted text must not be blank.")
            if len(text) > MAX_DOCUMENT_SAVE_TEXT_CHARS:
                raise ValueError(
                    "Extracted text must be 200,000 characters or fewer."
                )

            provided_metadata = item.get("metadata", {})
            if not isinstance(provided_metadata, dict):
                raise ValueError("Document metadata must be an object.")
            supplied_format = provided_metadata.get("format")
            if supplied_format is not None and (
                not isinstance(supplied_format, str)
                or supplied_format.lower() != extension[1:]
            ):
                raise ValueError("Document metadata does not match the filename.")
            size_bytes = provided_metadata.get("size_bytes")
            if size_bytes is not None and (
                isinstance(size_bytes, bool)
                or not isinstance(size_bytes, int)
                or size_bytes < 0
                or size_bytes > MAX_DOCUMENT_FILE_BYTES
            ):
                raise ValueError("Document size metadata is invalid.")

            idempotency_key = item.get("idempotency_key")
            try:
                idempotency_key = str(UUID(idempotency_key))
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("A valid document save request ID is required.") from error

            metadata = {
                "original_filename": filename,
                "format": extension[1:],
                "text_characters": len(text),
                "text_utf8_bytes": len(text.encode("utf-8")),
            }
            if size_bytes is not None:
                metadata["size_bytes"] = size_bytes

            source = knowledge_store.save_text(
                filename,
                text,
                source_type="Document",
                filename=filename,
                metadata=metadata,
                idempotency_key=idempotency_key,
            )
            results.append({
                "filename": filename,
                "status": "success",
                "source": source,
            })
        except (DocumentExtractionError, ValueError) as error:
            results.append({
                "filename": filename,
                "status": "error",
                "error": " ".join(str(error).split())[:512],
            })
        except Exception:
            logger.exception("Unexpected document knowledge save failure")
            results.append({
                "filename": filename,
                "status": "error",
                "error": "Unable to save this document to knowledge. Please retry.",
            })
    return {"results": results}


@router.post("/api/knowledge/text")
def submit_text(request: TextSubmission, _user=Depends(require_admin)):
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="Text must not be blank.")

    title = (request.title or "").strip() or "Pasted text source"
    try:
        source = knowledge_store.save_text(title, request.text, request.notes)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"success": True, "source": source}


@router.get("/api/knowledge/sources")
def get_sources(_user=Depends(require_admin)):
    return {"sources": knowledge_store.list_sources()}


@router.get("/api/knowledge/sources/{source_id}")
def get_source(source_id: UUID, _user=Depends(require_admin)):
    source = knowledge_store.get_source(str(source_id))
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    return {"source": source}


@router.put("/api/knowledge/sources/{source_id}")
def update_source(
    source_id: UUID, request: TextSubmission, _user=Depends(require_admin)
):
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="Text must not be blank.")
    title = (request.title or "").strip() or "Pasted text source"
    try:
        source = knowledge_store.update_text(
            str(source_id), title, request.text, request.notes
        )
    except knowledge_store.DocupediaConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    return {"success": True, "source": source}


@router.delete("/api/knowledge/sources/{source_id}")
def delete_source(source_id: UUID, _user=Depends(require_admin)):
    if not knowledge_store.delete_source(str(source_id)):
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    return {"success": True, "source_id": str(source_id)}


@router.post("/api/knowledge/interview-context")
def get_interview_context(
    request: InterviewQuery, _user=Depends(require_employee)
):
    if not request.query.strip():
        raise HTTPException(status_code=422, detail="Query must not be blank.")

    if not knowledge_store.has_sources():
        raise HTTPException(
            status_code=404,
            detail="No knowledge sources are available for this interview yet.",
        )
    chunks = knowledge_store.search_chunks(request.query.strip())
    if not chunks:
        raise HTTPException(
            status_code=404,
            detail="No saved text matched this interview query.",
        )

    sources_by_id = {}
    for chunk in chunks:
        sources_by_id[chunk["source_id"]] = {
            "id": chunk["source_id"],
            "title": chunk["title"],
            "created_at": chunk["created_at"],
        }
    return {"chunks": chunks, "sources": list(sources_by_id.values())}
