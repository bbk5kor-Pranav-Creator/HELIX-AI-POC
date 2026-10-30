import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "data" / "knowledge.db"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 160
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "what", "when", "where", "which", "who", "with",
}


class DocupediaConflictError(ValueError):
    """A Docupedia request conflicts with a prior save or approval."""


class DocupediaNotFoundError(LookupError):
    """The requested structured Docupedia source does not exist."""


def _connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _connection():
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    with _connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_sources (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                original_text TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                    ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                UNIQUE (source_id, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source
                ON knowledge_chunks(source_id);
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_sources)")
        }
        if "notes" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_sources ADD COLUMN notes TEXT NOT NULL DEFAULT ''"
            )
        if "filename" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_sources ADD COLUMN filename TEXT NOT NULL DEFAULT ''"
            )
        if "metadata_json" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_sources ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "idempotency_key" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_sources ADD COLUMN idempotency_key TEXT"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_sources_idempotency_key
            ON knowledge_sources(idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS document_records (
                source_id TEXT PRIMARY KEY
                    REFERENCES knowledge_sources(id) ON DELETE CASCADE,
                original_text TEXT NOT NULL,
                extraction_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS document_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL
                    REFERENCES document_records(source_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                block_type TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                parent_block_id INTEGER
                    REFERENCES document_blocks(id) ON DELETE SET NULL,
                heading_level INTEGER CHECK (heading_level IS NULL OR heading_level > 0),
                parent_heading_block_id INTEGER
                    REFERENCES document_blocks(id) ON DELETE SET NULL,
                page_start INTEGER CHECK (page_start IS NULL OR page_start > 0),
                page_end INTEGER CHECK (page_end IS NULL OR page_end > 0),
                paragraph_index INTEGER
                    CHECK (paragraph_index IS NULL OR paragraph_index >= 0),
                table_index INTEGER
                    CHECK (table_index IS NULL OR table_index >= 0),
                row_index INTEGER CHECK (row_index IS NULL OR row_index >= 0),
                locator_json TEXT NOT NULL DEFAULT '{}',
                CHECK (page_start IS NULL OR page_end IS NULL OR page_end >= page_start),
                UNIQUE (source_id, ordinal),
                UNIQUE (id, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_document_blocks_source_order
                ON document_blocks(source_id, ordinal);
            CREATE INDEX IF NOT EXISTS idx_document_blocks_parent
                ON document_blocks(parent_block_id);
            CREATE INDEX IF NOT EXISTS idx_document_blocks_heading_parent
                ON document_blocks(parent_heading_block_id);

            CREATE TABLE IF NOT EXISTS document_block_cells (
                block_id INTEGER NOT NULL
                    REFERENCES document_blocks(id) ON DELETE CASCADE,
                cell_index INTEGER NOT NULL CHECK (cell_index >= 0),
                column_index INTEGER CHECK (column_index IS NULL OR column_index >= 0),
                text TEXT NOT NULL DEFAULT '',
                is_header INTEGER NOT NULL DEFAULT 0 CHECK (is_header IN (0, 1)),
                PRIMARY KEY (block_id, cell_index),
                UNIQUE (block_id, column_index)
            );

            CREATE TABLE IF NOT EXISTS document_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL
                    REFERENCES document_records(source_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                title TEXT NOT NULL,
                heading_block_id INTEGER
                    REFERENCES document_blocks(id) ON DELETE SET NULL,
                parent_section_id INTEGER
                    REFERENCES document_sections(id) ON DELETE CASCADE,
                first_block_ordinal INTEGER,
                last_block_ordinal INTEGER,
                is_synthetic INTEGER NOT NULL DEFAULT 0
                    CHECK (is_synthetic IN (0, 1)),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                CHECK (
                    first_block_ordinal IS NULL
                    OR last_block_ordinal IS NULL
                    OR last_block_ordinal >= first_block_ordinal
                ),
                UNIQUE (source_id, ordinal),
                UNIQUE (id, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_document_sections_source_order
                ON document_sections(source_id, ordinal);
            CREATE INDEX IF NOT EXISTS idx_document_sections_parent
                ON document_sections(parent_section_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_chunks_id_source
                ON knowledge_chunks(id, source_id);

            CREATE TABLE IF NOT EXISTS knowledge_chunk_sections (
                source_id TEXT NOT NULL
                    REFERENCES document_records(source_id) ON DELETE CASCADE,
                chunk_id INTEGER NOT NULL,
                section_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
                PRIMARY KEY (chunk_id, section_id),
                FOREIGN KEY (chunk_id, source_id)
                    REFERENCES knowledge_chunks(id, source_id) ON DELETE CASCADE,
                FOREIGN KEY (section_id, source_id)
                    REFERENCES document_sections(id, source_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_sections_section
                ON knowledge_chunk_sections(section_id, ordinal);

            CREATE TABLE IF NOT EXISTS knowledge_chunk_blocks (
                source_id TEXT NOT NULL
                    REFERENCES document_records(source_id) ON DELETE CASCADE,
                chunk_id INTEGER NOT NULL,
                block_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
                start_offset INTEGER CHECK (start_offset IS NULL OR start_offset >= 0),
                end_offset INTEGER CHECK (end_offset IS NULL OR end_offset >= 0),
                PRIMARY KEY (chunk_id, block_id),
                CHECK (
                    start_offset IS NULL
                    OR end_offset IS NULL
                    OR end_offset >= start_offset
                ),
                FOREIGN KEY (chunk_id, source_id)
                    REFERENCES knowledge_chunks(id, source_id) ON DELETE CASCADE,
                FOREIGN KEY (block_id, source_id)
                    REFERENCES document_blocks(id, source_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_blocks_block
                ON knowledge_chunk_blocks(block_id, ordinal);
            """
        )


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    return [
        chunk
        for chunk, _start, _end in _chunk_text_with_offsets(
            text, chunk_size=chunk_size, overlap=overlap
        )
    ]


def _chunk_text_with_offsets(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size and overlap are invalid.")

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        chunk_start = start
        chunk_end = end
        while chunk_start < chunk_end and text[chunk_start].isspace():
            chunk_start += 1
        while chunk_end > chunk_start and text[chunk_end - 1].isspace():
            chunk_end -= 1
        chunk = text[chunk_start:chunk_end]
        if chunk:
            chunks.append((chunk, chunk_start, chunk_end))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def save_text(
    title, text, notes="", *, source_type="paste-text", filename="", metadata=None,
    idempotency_key=None,
):
    initialize_database()
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("Text must contain non-whitespace characters.")

    source_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    if idempotency_key is not None and not str(idempotency_key).strip():
        raise ValueError("Idempotency key must not be blank.")

    try:
        with _connection() as connection:
            existing = None
            if idempotency_key is not None:
                existing = connection.execute(
                    """
                    SELECT id, title, source_type, original_text, notes,
                           filename, metadata_json, created_at
                    FROM knowledge_sources WHERE idempotency_key = ?
                    """,
                    (str(idempotency_key),),
                ).fetchone()
            if existing is not None:
                if (
                    existing["title"] != title
                    or existing["source_type"] != source_type
                    or existing["original_text"] != text
                    or existing["notes"] != notes
                    or existing["filename"] != filename
                    or existing["metadata_json"] != metadata_json
                ):
                    raise ValueError(
                        "This document save request was already used with different content."
                    )
                source_id = existing["id"]
                created_at = existing["created_at"]
            else:
                connection.execute(
                    """
                    INSERT INTO knowledge_sources
                        (id, title, source_type, original_text, notes, created_at,
                         filename, metadata_json, idempotency_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id, title, source_type, text, notes, created_at,
                        filename, metadata_json, idempotency_key,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO knowledge_chunks (source_id, chunk_index, content)
                    VALUES (?, ?, ?)
                    """,
                    [(source_id, index, chunk) for index, chunk in enumerate(chunks)],
                )
    except sqlite3.IntegrityError:
        if idempotency_key is None:
            raise
        existing = get_source_by_idempotency_key(str(idempotency_key))
        if existing is None:
            raise
        if (
            existing["title"] != title
            or existing["source_type"] != source_type
            or existing["original_text"] != text
            or existing["notes"] != notes
            or existing["filename"] != filename
            or existing.get("metadata", {}) != (metadata or {})
        ):
            raise ValueError(
                "This document save request was already used with different content."
            )
        source_id = existing["id"]
        created_at = existing["created_at"]

    source = {
        "id": source_id,
        "title": title,
        "source_type": source_type,
        "filename": filename,
        "created_at": created_at,
        "char_count": len(text),
        "chunk_count": len(chunks),
        "notes": notes,
    }
    if metadata is not None:
        source["metadata"] = metadata
    return source


def save_structured_document(
    structured_document, filename, title=None, *, idempotency_key=None
):
    """Persist an extracted Docupedia document as pending, source-linked knowledge."""
    initialize_database()
    if not isinstance(structured_document, dict):
        raise ValueError("Structured document must be an object.")
    original_text = structured_document.get("original_text")
    blocks = structured_document.get("blocks")
    file_format = str(structured_document.get("format", "")).lower().lstrip(".")
    supported_formats = {"pdf", "docx", "doc", "txt"}
    if not isinstance(original_text, str) or not original_text.strip():
        raise ValueError("Structured document must contain extracted text.")
    if not isinstance(blocks, list):
        raise ValueError("Structured document blocks must be a list.")
    if file_format not in supported_formats:
        raise ValueError("Unsupported Docupedia document format.")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("Document filename must not be blank.")
    if Path(filename).suffix.lower() != f".{file_format}":
        raise ValueError("Document filename extension does not match its format.")

    ordered_blocks = []
    seen_ordinals = set()
    for fallback_ordinal, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValueError("Every structured document block must be an object.")
        ordinal = block.get("ordinal", fallback_ordinal)
        if not isinstance(ordinal, int) or ordinal < 0 or ordinal in seen_ordinals:
            raise ValueError("Structured document block ordinals are invalid.")
        if not isinstance(block.get("block_type"), str):
            raise ValueError("Every structured document block needs a block type.")
        block_text = block.get("text", "")
        if not isinstance(block_text, str):
            raise ValueError("Structured document block text must be a string.")
        if not isinstance(block.get("cells", []), list):
            raise ValueError("Structured document block cells must be a list.")
        seen_ordinals.add(ordinal)
        ordered_blocks.append((ordinal, block))
    ordered_blocks.sort(key=lambda item: item[0])

    source_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    source_title = title.strip() if isinstance(title, str) and title.strip() else filename
    request_digest = hashlib.sha256(json.dumps(
        {"document": structured_document, "filename": filename, "title": source_title},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    if idempotency_key is not None:
        if not str(idempotency_key).strip():
            raise ValueError("Idempotency key must not be blank.")
        idempotency_key = f"docupedia:{idempotency_key}"
    source_metadata = {
        "docupedia": {
            "approval_status": "pending",
            "save_digest": request_digest,
        },
        "format": file_format,
        "original_filename": filename,
    }
    extraction_metadata = {
        key: value
        for key, value in structured_document.items()
        if key not in {"original_text", "blocks"}
    }
    extraction_metadata_json = json.dumps(extraction_metadata, ensure_ascii=False)
    source_metadata_json = json.dumps(source_metadata, ensure_ascii=False)

    with _connection() as connection:
        # Serialize retries before reading the key, including concurrent requests.
        connection.execute("BEGIN IMMEDIATE")
        if idempotency_key is not None:
            existing = connection.execute(
                """
                SELECT s.*, LENGTH(s.original_text) AS char_count,
                       (SELECT COUNT(*) FROM knowledge_chunks WHERE source_id = s.id)
                           AS chunk_count
                FROM knowledge_sources AS s WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                existing_metadata = json.loads(existing["metadata_json"])
                if (
                    existing["source_type"] != "Docupedia"
                    or existing_metadata.get("docupedia", {}).get("save_digest")
                    != request_digest
                ):
                    raise DocupediaConflictError(
                        "This Docupedia save request was already used with different content."
                    )
                return _source_from_row(existing)
        connection.execute(
            """
            INSERT INTO knowledge_sources
                (id, title, source_type, original_text, notes, created_at,
                 filename, metadata_json, idempotency_key)
            VALUES (?, ?, 'Docupedia', '', '', ?, ?, ?, ?)
            """,
            (
                source_id,
                source_title,
                created_at,
                filename,
                source_metadata_json,
                idempotency_key,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_records
                (source_id, original_text, extraction_metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                source_id,
                original_text,
                extraction_metadata_json,
                created_at,
                created_at,
            ),
        )

        block_ids = {}
        heading_stack = []
        section_stack = []
        sections = []
        block_sections = {}
        block_metadata = {}
        preamble_section_id = None

        def create_section(section_title, heading_block_id, parent_section_id, ordinal,
                           is_synthetic):
            cursor = connection.execute(
                """
                INSERT INTO document_sections
                    (source_id, ordinal, title, heading_block_id, parent_section_id,
                     first_block_ordinal, last_block_ordinal, is_synthetic, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    ordinal,
                    section_title,
                    heading_block_id,
                    parent_section_id,
                    None,
                    None,
                    int(is_synthetic),
                    json.dumps({"reason": "no_document_heading"} if is_synthetic else {}),
                ),
            )
            section_id = cursor.lastrowid
            sections.append({
                "id": section_id,
                "ordinal": ordinal,
                "title": section_title,
                "parent_id": parent_section_id,
                "first": None,
                "last": None,
                "level": None,
                "synthetic": is_synthetic,
            })
            return section_id

        for ordinal, block in ordered_blocks:
            block_type = block["block_type"]
            is_heading = block_type == "heading"
            heading_level = block.get("heading_level") if is_heading else None
            if is_heading:
                heading_level = heading_level if isinstance(heading_level, int) and heading_level > 0 else 1
                while heading_stack and heading_stack[-1]["level"] >= heading_level:
                    heading_stack.pop()
                    section_stack.pop()
                if not heading_stack and preamble_section_id in section_stack:
                    section_stack.remove(preamble_section_id)
            elif not heading_stack and preamble_section_id is None and not sections:
                preamble_section_id = create_section(
                    "Document content", None, None, len(sections), True
                )
                section_stack.append(preamble_section_id)

            parent_ordinal = block.get("parent_block_ordinal")
            if parent_ordinal is not None:
                parent_block_id = block_ids.get(parent_ordinal)
                if parent_block_id is None:
                    raise ValueError("A document block references a missing or later parent.")
            else:
                parent_block_id = None

            if is_heading:
                parent_heading_id = heading_stack[-1]["block_id"] if heading_stack else None
            elif heading_stack:
                parent_heading_id = heading_stack[-1]["block_id"]
            else:
                parent_heading_id = None
            explicit_heading_ordinal = block.get("parent_heading_block_ordinal")
            if explicit_heading_ordinal is not None:
                parent_heading_id = block_ids.get(explicit_heading_ordinal)
                if parent_heading_id is None:
                    raise ValueError("A document block references a missing heading parent.")

            known_fields = {
                "ordinal", "block_type", "text", "parent_block_ordinal",
                "parent_heading_block_ordinal", "heading_level", "page_start",
                "page_end", "paragraph_index", "table_index", "row_index",
                "locator", "cells",
            }
            extra_fields = {
                key: value for key, value in block.items() if key not in known_fields
            }
            cell_extras = {}
            for cell_index, cell in enumerate(block.get("cells", [])):
                if not isinstance(cell, dict):
                    raise ValueError("Structured table cells must be objects.")
                stored_cell_index = cell.get("cell_index", cell_index)
                if not isinstance(stored_cell_index, int) or stored_cell_index < 0:
                    raise ValueError("Structured table cell indexes are invalid.")
                cell_extras[stored_cell_index] = {
                    key: value for key, value in cell.items()
                    if key not in {"cell_index", "column_index", "text", "is_header"}
                }
                cell_extras[stored_cell_index]["original_keys"] = list(cell.keys())
            locator_json = json.dumps(
                {
                    "locator": block.get("locator", {}),
                    "extra": extra_fields,
                    "cell_extras": cell_extras,
                    "original_keys": list(block.keys()),
                },
                ensure_ascii=False,
            )
            cursor = connection.execute(
                """
                INSERT INTO document_blocks
                    (source_id, ordinal, block_type, text, parent_block_id,
                     heading_level, parent_heading_block_id, page_start, page_end,
                     paragraph_index, table_index, row_index, locator_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    ordinal,
                    block_type,
                    block.get("text", ""),
                    parent_block_id,
                    heading_level,
                    parent_heading_id,
                    block.get("page_start"),
                    block.get("page_end"),
                    block.get("paragraph_index"),
                    block.get("table_index"),
                    block.get("row_index"),
                    locator_json,
                ),
            )
            block_id = cursor.lastrowid
            block_ids[ordinal] = block_id
            block_metadata[ordinal] = {
                "id": block_id,
                "block": block,
                "locator": json.loads(locator_json),
            }

            for cell_index, cell in enumerate(block.get("cells", [])):
                stored_cell_index = cell.get("cell_index", cell_index)
                connection.execute(
                    """
                    INSERT INTO document_block_cells
                        (block_id, cell_index, column_index, text, is_header)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        block_id,
                        stored_cell_index,
                        cell.get("column_index"),
                        cell.get("text", ""),
                        int(bool(cell.get("is_header", False))),
                    ),
                )

            if is_heading:
                for active_section_id in section_stack:
                    section = next(
                        item for item in sections if item["id"] == active_section_id
                    )
                    if section["first"] is None:
                        section["first"] = ordinal
                    section["last"] = ordinal
                parent_section_id = section_stack[-1] if section_stack else None
                section_id = create_section(
                    block["text"] or "Untitled section",
                    block_id,
                    parent_section_id,
                    len(sections),
                    False,
                )
                sections[-1]["first"] = ordinal
                sections[-1]["last"] = ordinal
                sections[-1]["level"] = heading_level
                section_stack.append(section_id)
                heading_stack.append({
                    "level": heading_level,
                    "block_id": block_id,
                    "section_id": section_id,
                })
            elif section_stack:
                for section_id in section_stack:
                    section = next(item for item in sections if item["id"] == section_id)
                    if section["first"] is None:
                        section["first"] = ordinal
                    section["last"] = ordinal
            block_sections[ordinal] = list(section_stack)

        if not sections:
            create_section("Document content", None, None, 0, True)

        for section in sections:
            connection.execute(
                """
                UPDATE document_sections
                SET first_block_ordinal = ?, last_block_ordinal = ?
                WHERE id = ?
                """,
                (section["first"], section["last"], section["id"]),
            )

        chunks_to_insert = []
        current_chunk_index = 0
        leaf_sections = [section for section in sections]
        for section in leaf_sections:
            section_id = section["id"]
            content_blocks = []
            for ordinal, block in ordered_blocks:
                memberships = block_sections.get(ordinal, [])
                if not memberships or memberships[-1] != section_id:
                    continue
                block_text = block.get("text", "")
                if not block_text and block.get("cells"):
                    block_text = " | ".join(
                        str(cell.get("text", "")) for cell in block["cells"]
                    )
                if block_text.strip():
                    content_blocks.append((ordinal, block_text))

            if not content_blocks:
                continue
            section_content_parts = []
            content_spans = []
            position = 0
            for block_ordinal, block_text in content_blocks:
                if section_content_parts:
                    section_content_parts.append("\n")
                    position += 1
                start_offset = position
                section_content_parts.append(block_text)
                position += len(block_text)
                content_spans.append((block_ordinal, start_offset, position))
            section_content = "".join(section_content_parts)

            for content, chunk_start, chunk_end in _chunk_text_with_offsets(section_content):
                chunk_cursor = connection.execute(
                    """
                    INSERT INTO knowledge_chunks (source_id, chunk_index, content)
                    VALUES (?, ?, ?)
                    """,
                    (source_id, current_chunk_index, content),
                )
                chunk_id = chunk_cursor.lastrowid
                relevant_ordinals = []
                for block_ordinal, block_start, block_end in content_spans:
                    if block_start < chunk_end and block_end > chunk_start:
                        relevant_ordinals.append(block_ordinal)
                relevant_section_ids = []
                for block_ordinal in relevant_ordinals:
                    for linked_section_id in block_sections[block_ordinal]:
                        if linked_section_id not in relevant_section_ids:
                            relevant_section_ids.append(linked_section_id)
                if section_id not in relevant_section_ids:
                    relevant_section_ids.insert(0, section_id)
                connection.executemany(
                    """
                    INSERT INTO knowledge_chunk_sections
                        (source_id, chunk_id, section_id, ordinal)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (source_id, chunk_id, linked_id, link_ordinal)
                        for link_ordinal, linked_id in enumerate(relevant_section_ids)
                    ],
                )

                linked_blocks = []
                for block_ordinal, block_start, block_end in content_spans:
                    if block_start >= chunk_end or block_end <= chunk_start:
                        continue
                    block_id = block_ids[block_ordinal]
                    local_start = max(chunk_start, block_start) - block_start
                    local_end = min(chunk_end, block_end) - block_start
                    linked_blocks.append((block_id, local_start, local_end))
                    block = block_metadata[block_ordinal]["block"]
                    parent_ordinal = block.get("parent_block_ordinal")
                    if parent_ordinal is not None:
                        parent_id = block_ids[parent_ordinal]
                        if all(item[0] != parent_id for item in linked_blocks):
                            linked_blocks.append((parent_id, None, None))
                connection.executemany(
                    """
                    INSERT INTO knowledge_chunk_blocks
                        (source_id, chunk_id, block_id, ordinal, start_offset, end_offset)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (source_id, chunk_id, block_id, link_ordinal, start_offset, end_offset)
                        for link_ordinal, (block_id, start_offset, end_offset)
                        in enumerate(linked_blocks)
                    ],
                )
                current_chunk_index += 1
                chunks_to_insert.append(content)

    return {
        "id": source_id,
        "title": source_title,
        "source_type": "Docupedia",
        "filename": filename,
        "created_at": created_at,
        "char_count": 0,
        "chunk_count": len(chunks_to_insert),
        "metadata": source_metadata,
    }


def get_structured_document(source_id):
    """Reconstruct the persisted extraction in its original block reading order."""
    initialize_database()
    with _connection() as connection:
        record = connection.execute(
            """
            SELECT original_text, extraction_metadata_json
            FROM document_records WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        if record is None:
            return None
        rows = connection.execute(
            """
            SELECT id, ordinal, block_type, text, parent_block_id, heading_level,
                   parent_heading_block_id, page_start, page_end, paragraph_index,
                   table_index, row_index, locator_json
            FROM document_blocks WHERE source_id = ? ORDER BY ordinal
            """,
            (source_id,),
        ).fetchall()
        id_to_ordinal = {row["id"]: row["ordinal"] for row in rows}
        reconstructed_blocks = []
        for row in rows:
            locator_data = json.loads(row["locator_json"])
            original_keys = locator_data.get("original_keys", [])
            block = {
                "ordinal": row["ordinal"],
                "block_type": row["block_type"],
                "text": row["text"],
            }
            mapped_values = {
                "heading_level": row["heading_level"],
                "parent_block_ordinal": id_to_ordinal.get(row["parent_block_id"]),
                "parent_heading_block_ordinal": id_to_ordinal.get(
                    row["parent_heading_block_id"]
                ),
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "paragraph_index": row["paragraph_index"],
                "table_index": row["table_index"],
                "row_index": row["row_index"],
            }
            for key, value in mapped_values.items():
                if key in original_keys:
                    block[key] = value
            if "locator" in original_keys:
                block["locator"] = locator_data.get("locator", {})
            block.update(locator_data.get("extra", {}))
            if "cells" in original_keys:
                cell_rows = connection.execute(
                    """
                    SELECT cell_index, column_index, text, is_header
                    FROM document_block_cells WHERE block_id = ? ORDER BY cell_index
                    """,
                    (row["id"],),
                ).fetchall()
                cell_extras = locator_data.get("cell_extras", {})
                cells = []
                for cell_row in cell_rows:
                    cell_extra = cell_extras.get(str(cell_row["cell_index"]), {})
                    original_cell_keys = cell_extra.get("original_keys", [])
                    cell = {}
                    for key, value in (
                        ("cell_index", cell_row["cell_index"]),
                        ("column_index", cell_row["column_index"]),
                        ("text", cell_row["text"]),
                    ):
                        if key in original_cell_keys:
                            cell[key] = value
                    if "is_header" in original_cell_keys:
                        cell["is_header"] = bool(cell_row["is_header"])
                    cell.update({
                        key: value
                        for key, value in cell_extra.items()
                        if key != "original_keys"
                    })
                    cells.append(cell)
                block["cells"] = cells
            reconstructed_blocks.append(block)
    metadata = json.loads(record["extraction_metadata_json"])
    return {
        **metadata,
        "original_text": record["original_text"],
        "blocks": reconstructed_blocks,
    }


def approve_structured_document(source_id, text, *, idempotency_key):
    """Atomically publish reviewed wording without modifying the raw extraction."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Approved text must not be blank.")
    if not idempotency_key or not str(idempotency_key).strip():
        raise ValueError("An approval request ID is required.")
    chunks = _chunk_text_with_offsets(text)
    initialize_database()
    with _connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT s.*, LENGTH(s.original_text) AS char_count,
                   (SELECT COUNT(*) FROM knowledge_chunks WHERE source_id = s.id)
                       AS chunk_count
            FROM knowledge_sources AS s
            JOIN document_records AS d ON d.source_id = s.id
            WHERE s.id = ? AND s.source_type = 'Docupedia'
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            raise DocupediaNotFoundError("Docupedia document not found.")
        source = _source_from_row(row)
        metadata = source.get("metadata", {})
        approval = metadata.get("docupedia", {})
        if approval.get("approval_status") != "pending":
            if (
                approval.get("approval_status") == "approved"
                and approval.get("approval_request_id") == str(idempotency_key)
                and source["original_text"] == text
            ):
                return source
            raise DocupediaConflictError(
                "This Docupedia document has already been approved with a different request."
            )

        approved_at = datetime.now(timezone.utc).isoformat()
        approval.update({
            "approval_status": "approved",
            "approval_request_id": str(idempotency_key),
            "approved_at": approved_at,
            "chunk_provenance": "exact_source_text_matches_only",
        })
        metadata["docupedia"] = approval
        connection.execute(
            "UPDATE knowledge_sources SET original_text = ?, metadata_json = ? WHERE id = ?",
            (text, json.dumps(metadata, ensure_ascii=False), source_id),
        )
        connection.execute(
            "DELETE FROM knowledge_chunks WHERE source_id = ?", (source_id,)
        )
        source_blocks = connection.execute(
            """
            SELECT id, ordinal, text, parent_block_id, parent_heading_block_id
            FROM document_blocks WHERE source_id = ? ORDER BY ordinal
            """,
            (source_id,),
        ).fetchall()
        sections = connection.execute(
            """
            SELECT id, first_block_ordinal, last_block_ordinal
            FROM document_sections WHERE source_id = ? ORDER BY ordinal
            """,
            (source_id,),
        ).fetchall()
        matches = []
        for block in source_blocks:
            if not block["text"].strip():
                continue
            start = text.find(block["text"])
            while start >= 0:
                matches.append((block, start, start + len(block["text"])))
                start = text.find(block["text"], start + len(block["text"]))

        for index, (content, chunk_start, chunk_end) in enumerate(chunks):
            chunk_id = connection.execute(
                """
                INSERT INTO knowledge_chunks (source_id, chunk_index, content)
                VALUES (?, ?, ?)
                """,
                (source_id, index, content),
            ).lastrowid
            block_links = {}
            section_links = set()
            for block, start, end in matches:
                if start >= chunk_end or end <= chunk_start:
                    continue
                block_links[block["id"]] = (
                    max(chunk_start, start) - start, min(chunk_end, end) - start
                )
                for parent_id in (block["parent_block_id"], block["parent_heading_block_id"]):
                    if parent_id is not None:
                        block_links.setdefault(parent_id, (None, None))
                for section in sections:
                    if (
                        section["first_block_ordinal"] is not None
                        and section["first_block_ordinal"] <= block["ordinal"]
                        <= section["last_block_ordinal"]
                    ):
                        section_links.add(section["id"])
            connection.executemany(
                """
                INSERT INTO knowledge_chunk_blocks
                    (source_id, chunk_id, block_id, ordinal, start_offset, end_offset)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (source_id, chunk_id, block_id, ordinal, start, end)
                    for ordinal, (block_id, (start, end)) in enumerate(block_links.items())
                ],
            )
            connection.executemany(
                """
                INSERT INTO knowledge_chunk_sections
                    (source_id, chunk_id, section_id, ordinal)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (source_id, chunk_id, section["id"], ordinal)
                    for ordinal, section in enumerate(
                        section for section in sections if section["id"] in section_links
                    )
                ],
            )
        connection.execute(
            "UPDATE document_records SET updated_at = ? WHERE source_id = ?",
            (approved_at, source_id),
        )
    return get_source(source_id)


def get_source_by_idempotency_key(idempotency_key):
    initialize_database()
    with _connection() as connection:
        row = connection.execute(
            "SELECT id FROM knowledge_sources WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
    return get_source(row["id"]) if row is not None else None


def _source_from_row(row):
    source = dict(row)
    metadata = json.loads(source.pop("metadata_json"))
    if metadata:
        source["metadata"] = metadata
    return source


def get_source(source_id):
    initialize_database()
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT s.*, LENGTH(s.original_text) AS char_count,
                   (SELECT COUNT(*) FROM knowledge_chunks WHERE source_id = s.id)
                       AS chunk_count
            FROM knowledge_sources AS s WHERE s.id = ?
            """,
            (source_id,),
        ).fetchone()
    if row is None:
        return None
    return _source_from_row(row)


def update_text(source_id, title, text, notes):
    initialize_database()
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("Text must contain non-whitespace characters.")
    with _connection() as connection:
        source = connection.execute(
            "SELECT source_type FROM knowledge_sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source is not None and source["source_type"] == "Docupedia":
            raise DocupediaConflictError(
                "Docupedia content must be published through its approval endpoint."
            )
        result = connection.execute(
            """
            UPDATE knowledge_sources SET title = ?, original_text = ?, notes = ?
            WHERE id = ?
            """,
            (title, text, notes, source_id),
        )
        if result.rowcount == 0:
            return None
        connection.execute("DELETE FROM knowledge_chunks WHERE source_id = ?", (source_id,))
        connection.executemany(
            """
            INSERT INTO knowledge_chunks (source_id, chunk_index, content)
            VALUES (?, ?, ?)
            """,
            [(source_id, index, chunk) for index, chunk in enumerate(chunks)],
        )
    return get_source(source_id)


def delete_source(source_id):
    initialize_database()
    with _connection() as connection:
        result = connection.execute(
            "DELETE FROM knowledge_sources WHERE id = ?", (source_id,)
        )
    return result.rowcount > 0


def list_sources():
    initialize_database()
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.title, s.source_type, s.created_at, s.notes, s.filename,
                   LENGTH(s.original_text) AS char_count,
                   COUNT(c.id) AS chunk_count
            FROM knowledge_sources AS s
            LEFT JOIN knowledge_chunks AS c ON c.source_id = s.id
            GROUP BY s.id
            ORDER BY s.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def search_chunks(query, limit=6):
    terms = []
    seen_terms = set()
    for raw_term in re.findall(r"[A-Za-z0-9]+", query):
        term = raw_term.lower()
        if len(term) > 1 and term not in STOP_WORDS and term not in seen_terms:
            terms.append(term)
            seen_terms.add(term)
            if len(terms) == 32:
                break
    if not terms:
        return []

    initialize_database()
    conditions = " OR ".join("LOWER(c.content) LIKE ?" for _ in terms)
    parameters = [f"%{term}%" for term in terms]
    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT c.source_id, c.chunk_index, c.content,
                   s.title, s.created_at
            FROM knowledge_chunks AS c
            JOIN knowledge_sources AS s ON s.id = c.source_id
            WHERE ({conditions})
              AND (
                  s.source_type <> 'Docupedia'
                  OR (
                      json_extract(s.metadata_json, '$.docupedia.approval_status') = 'approved'
                      AND LENGTH(TRIM(s.original_text)) > 0
                  )
              )
            """,
            parameters,
        ).fetchall()

    ranked = []
    for row in rows:
        content = row["content"].lower()
        score = sum(content.count(term) for term in terms)
        ranked.append((score, row))
    ranked.sort(
        key=lambda item: (item[0], item[1]["created_at"], -item[1]["chunk_index"]),
        reverse=True,
    )

    return [
        {
            "source_id": row["source_id"],
            "title": row["title"],
            "chunk_index": row["chunk_index"],
            "content": row["content"],
            "created_at": row["created_at"],
            "score": score,
        }
        for score, row in ranked[:limit]
        if score > 0
    ]


def has_sources():
    initialize_database()
    with _connection() as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM knowledge_sources)"
        ).fetchone()
    return bool(row[0])
