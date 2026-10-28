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


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
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
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def save_text(
    title, text, notes="", *, source_type="paste-text", filename="", metadata=None,
):
    initialize_database()
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("Text must contain non-whitespace characters.")

    source_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_sources
                (id, title, source_type, original_text, notes, created_at, filename, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id, title, source_type, text, notes, created_at, filename,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_chunks (source_id, chunk_index, content)
            VALUES (?, ?, ?)
            """,
            [(source_id, index, chunk) for index, chunk in enumerate(chunks)],
        )

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
    source = dict(row)
    metadata = json.loads(source.pop("metadata_json"))
    if metadata:
        source["metadata"] = metadata
    return source


def update_text(source_id, title, text, notes):
    initialize_database()
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("Text must contain non-whitespace characters.")
    with _connection() as connection:
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
            WHERE {conditions}
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
