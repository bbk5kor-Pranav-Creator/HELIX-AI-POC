"""Admin-only Docupedia preview, pending ingestion, retrieval, and approval.

Preview accepts multipart ``files``. Save accepts ``filename``,
``structured_document`` (the preview result), and a UUID ``idempotency_key``.
Approval accepts ``text`` and a UUID ``idempotency_key``. Identical retries are
safe; changed saves or approvals return HTTP 409. Preview responses and save
payloads have an explicit 16 MiB UTF-8 JSON limit, with no content truncation.
Approval uses the existing 200,000-character reviewed-text limit. Pending source
chunks are replaced, not published, and only exact source wording receives
block-offset provenance. Raw extraction blocks are never rewritten.
"""

import json
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

import knowledge_store
from document_extractor import (
    DOCPEDIA_FORMATS,
    DocumentExtractionError,
    extract_structured_document,
)
from knowledge_routes import (
    DOCUMENT_ERROR_RESPONSE_RESERVE_BYTES,
    DOCUMENT_READ_BLOCK_BYTES,
    MAX_DOCUMENT_FILE_BYTES,
    MAX_DOCUMENT_PREVIEW_RESPONSE_BYTES,
    MAX_DOCUMENT_SAVE_BATCH_SIZE,
    MAX_DOCUMENT_SAVE_TEXT_CHARS,
    _document_filename,
    _serialized_preview_size,
    require_admin,
)


router = APIRouter(prefix="/api/knowledge/docupedia", tags=["Docupedia"])
logger = logging.getLogger(__name__)


class DocumentCell(BaseModel):
    model_config = ConfigDict(extra="allow")
    cell_index: int = Field(ge=0, strict=True)
    column_index: Optional[int] = Field(default=None, ge=0, strict=True)
    text: str = Field(strict=True)
    is_header: bool = Field(default=False, strict=True)


class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="allow")
    ordinal: int = Field(ge=0, strict=True)
    block_type: str = Field(min_length=1, strict=True)
    text: str = Field(strict=True)
    heading_level: Optional[int] = Field(default=None, gt=0, strict=True)
    parent_block_ordinal: Optional[int] = Field(default=None, ge=0, strict=True)
    parent_heading_block_ordinal: Optional[int] = Field(default=None, ge=0, strict=True)
    page_start: Optional[int] = Field(default=None, gt=0, strict=True)
    page_end: Optional[int] = Field(default=None, gt=0, strict=True)
    paragraph_index: Optional[int] = Field(default=None, ge=0, strict=True)
    table_index: Optional[int] = Field(default=None, ge=0, strict=True)
    row_index: Optional[int] = Field(default=None, ge=0, strict=True)
    locator: dict = Field(default_factory=dict)
    cells: list[DocumentCell] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_structure(self):
        if self.block_type == "heading" and self.heading_level is None:
            raise ValueError("Heading blocks require a heading level.")
        if (
            self.page_start is not None and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("Page end must not precede page start.")
        indexes = [cell.cell_index for cell in self.cells]
        columns = [cell.column_index for cell in self.cells if cell.column_index is not None]
        if len(set(indexes)) != len(indexes) or len(set(columns)) != len(columns):
            raise ValueError("Table cell and column indexes must be unique.")
        if indexes != sorted(indexes):
            raise ValueError("Table cells must be in reading order.")
        return self


class StructuredExtraction(BaseModel):
    model_config = ConfigDict(extra="allow")
    format: Literal["pdf", "docx", "doc", "txt"]
    original_text: str = Field(min_length=1, strict=True)
    blocks: list[DocumentBlock] = Field(min_length=1)
    warnings: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_blocks(self):
        if not self.original_text.strip() or not any(
            block.text.strip() or any(cell.text.strip() for cell in block.cells)
            for block in self.blocks
        ):
            raise ValueError("Structured extraction must contain readable source content.")
        seen = {}
        previous_ordinal = -1
        for block in self.blocks:
            if block.ordinal <= previous_ordinal:
                raise ValueError("Document block ordinals must be unique and in reading order.")
            if block.parent_block_ordinal is not None and block.parent_block_ordinal not in seen:
                raise ValueError("A block parent must precede its child.")
            if block.parent_heading_block_ordinal is not None:
                parent = seen.get(block.parent_heading_block_ordinal)
                if parent is None or parent.block_type != "heading":
                    raise ValueError("A heading parent must be an earlier heading block.")
            seen[block.ordinal] = block
            previous_ordinal = block.ordinal
        return self


class DocupediaSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255, strict=True)
    title: Optional[str] = Field(default=None, max_length=120, strict=True)
    structured_document: StructuredExtraction
    idempotency_key: UUID


class DocupediaApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_DOCUMENT_SAVE_TEXT_CHARS, strict=True)
    idempotency_key: UUID


def _docupedia_filename(filename):
    extension = _document_filename(filename)
    if extension not in DOCPEDIA_FORMATS:
        raise DocumentExtractionError("Docupedia supports PDF, DOCX, DOC and TXT only.")
    return extension


@router.post("/preview")
async def preview_docupedia(
    files: list[UploadFile] = File(..., min_length=1, max_length=MAX_DOCUMENT_SAVE_BATCH_SIZE),
    _user=Depends(require_admin),
):
    results = []
    for index, upload in enumerate(files):
        result = {
            "filename": (upload.filename or "")[:255],
            "status": "error",
            "structured_document": None,
            "metadata": {},
        }
        try:
            extension = _docupedia_filename(upload.filename or "")
            size = 0
            with tempfile.TemporaryDirectory(prefix="helix_docupedia_upload_") as directory:
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
                structured = await run_in_threadpool(extract_structured_document, path)
                result.update({
                    "status": "success",
                    "structured_document": structured,
                    "metadata": {"format": extension[1:], "size_bytes": size},
                })
                reserved = (len(files) - index - 1) * DOCUMENT_ERROR_RESPONSE_RESERVE_BYTES
                if _serialized_preview_size(results + [result]) + reserved > MAX_DOCUMENT_PREVIEW_RESPONSE_BYTES:
                    raise DocumentExtractionError(
                        "Structured extraction was not returned because the preview "
                        "response would exceed its 16 MiB limit. Upload fewer or smaller documents."
                    )
        except DocumentExtractionError as error:
            result.update({
                "status": "error", "structured_document": None, "error": str(error),
            })
        except OSError:
            logger.exception("Docupedia preview file handling failed")
            result.update({
                "status": "error", "structured_document": None,
                "error": "Unable to prepare this document for extraction.",
            })
        except Exception:
            logger.exception("Unexpected Docupedia preview failure")
            result.update({
                "status": "error", "structured_document": None,
                "error": "Document extraction failed unexpectedly. Please retry.",
            })
        finally:
            await upload.close()
        results.append(result)
    return {"results": results}


@router.post("/save")
def save_docupedia(request: DocupediaSaveRequest, _user=Depends(require_admin)):
    structured = request.structured_document.model_dump(exclude_unset=True)
    try:
        extension = _docupedia_filename(request.filename)
        if extension[1:] != structured["format"]:
            raise ValueError("Document format does not match the filename.")
        payload_bytes = len(json.dumps(
            structured, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8"))
        if payload_bytes > MAX_DOCUMENT_PREVIEW_RESPONSE_BYTES:
            raise HTTPException(
                status_code=413, detail="Structured extraction exceeds the 16 MiB save limit."
            )
        source = knowledge_store.save_structured_document(
            structured, request.filename, request.title,
            idempotency_key=str(request.idempotency_key),
        )
    except knowledge_store.DocupediaConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except sqlite3.Error as error:
        logger.exception("Docupedia pending save failed")
        raise HTTPException(status_code=500, detail="Unable to save Docupedia document.") from error
    return {
        "success": True, "source": source,
        "approval_status": source["metadata"]["docupedia"]["approval_status"],
    }


@router.get("/{source_id}")
def get_docupedia(source_id: UUID, _user=Depends(require_admin)):
    source = knowledge_store.get_source(str(source_id))
    if source is None or source["source_type"] != "Docupedia":
        raise HTTPException(status_code=404, detail="Docupedia document not found.")
    structured = knowledge_store.get_structured_document(str(source_id))
    if structured is None:
        raise HTTPException(status_code=404, detail="Docupedia document not found.")
    return {
        "source": source,
        "structured_document": structured,
        "approval_status": source["metadata"]["docupedia"]["approval_status"],
    }


@router.post("/{source_id}/approve")
def approve_docupedia(
    source_id: UUID, request: DocupediaApprovalRequest, _user=Depends(require_admin),
):
    try:
        source = knowledge_store.approve_structured_document(
            str(source_id), request.text, idempotency_key=str(request.idempotency_key),
        )
    except knowledge_store.DocupediaNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except knowledge_store.DocupediaConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except sqlite3.Error as error:
        logger.exception("Docupedia approval failed")
        raise HTTPException(status_code=500, detail="Unable to approve Docupedia document.") from error
    return {"success": True, "source": source, "approval_status": "approved"}
