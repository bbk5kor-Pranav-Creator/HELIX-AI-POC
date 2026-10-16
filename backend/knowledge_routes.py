import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

import knowledge_store


router = APIRouter()
TOKEN_LIFETIME_SECONDS = 8 * 60 * 60


class TextSubmission(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    text: str = Field(..., min_length=1, max_length=200_000)
    notes: str = Field(default="", max_length=10_000)


class InterviewQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2_000)


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
    source = knowledge_store.update_text(
        str(source_id), title, request.text, request.notes
    )
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
