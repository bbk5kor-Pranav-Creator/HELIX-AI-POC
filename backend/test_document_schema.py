import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

import knowledge_store


class DocumentSchemaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "knowledge.db"
        self.original_database = knowledge_store.DATABASE_PATH
        knowledge_store.DATABASE_PATH = self.database
        self.addCleanup(self._restore_database)

    def tearDown(self):
        self.directory.cleanup()

    def _restore_database(self):
        knowledge_store.DATABASE_PATH = self.original_database

    def test_additive_migration_preserves_existing_sources_and_chunks(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE knowledge_sources (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE knowledge_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
                        ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE (source_id, chunk_index)
                )
                """
            )
            connection.execute(
                "INSERT INTO knowledge_sources VALUES (?, ?, ?, ?, ?)",
                ("legacy-source", "Legacy", "Audio", "Legacy content.", "2026-01-01"),
            )
            connection.execute(
                "INSERT INTO knowledge_chunks (source_id, chunk_index, content) "
                "VALUES (?, ?, ?)",
                ("legacy-source", 0, "Legacy content."),
            )

        knowledge_store.initialize_database()

        source = knowledge_store.get_source("legacy-source")
        self.assertEqual(source["original_text"], "Legacy content.")
        self.assertEqual(source["source_type"], "Audio")
        self.assertEqual(knowledge_store.search_chunks("Legacy content")[0]["content"],
                         "Legacy content.")
        with knowledge_store._connection() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({
                "document_records",
                "document_blocks",
                "document_block_cells",
                "document_sections",
                "knowledge_chunk_sections",
                "knowledge_chunk_blocks",
            }.issubset(tables))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM document_records"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_structured_document_crud_and_source_cascades(self):
        knowledge_store.initialize_database()
        source = knowledge_store.save_text(
            "Docupedia procedure",
            "Approved reviewed knowledge text.",
            source_type="Docupedia",
            filename="procedure.pdf",
            metadata={"format": "pdf"},
        )
        source_id = source["id"]
        approved_source = knowledge_store.get_source(source_id)
        raw_text = "1. Approval\n\nSource wording retained separately."
        with knowledge_store._connection() as connection:
            connection.execute(
                """
                INSERT INTO document_records
                    (source_id, original_text, extraction_metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    raw_text,
                    json.dumps({"extractor": "phase-1"}),
                    "2026-10-30T00:00:00+00:00",
                    "2026-10-30T00:00:00+00:00",
                ),
            )
            heading_id = connection.execute(
                """
                INSERT INTO document_blocks
                    (source_id, ordinal, block_type, text, heading_level, page_start, page_end)
                VALUES (?, 0, 'heading', '1. Approval', 1, 1, 1)
                """,
                (source_id,),
            ).lastrowid
            paragraph_id = connection.execute(
                """
                INSERT INTO document_blocks
                    (source_id, ordinal, block_type, text, parent_heading_block_id,
                     paragraph_index, page_start)
                VALUES (?, 1, 'paragraph', 'Manager approves the order.', ?, 0, 1)
                """,
                (source_id, heading_id),
            ).lastrowid
            table_id = connection.execute(
                """
                INSERT INTO document_blocks
                    (source_id, ordinal, block_type, page_start, table_index)
                VALUES (?, 2, 'table', 1, 0)
                """,
                (source_id,),
            ).lastrowid
            row_id = connection.execute(
                """
                INSERT INTO document_blocks
                    (source_id, ordinal, block_type, parent_block_id, row_index, page_start)
                VALUES (?, 3, 'table_row', ?, 0, 1)
                """,
                (source_id, table_id),
            ).lastrowid
            connection.executemany(
                """
                INSERT INTO document_block_cells
                    (block_id, cell_index, column_index, text, is_header)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (row_id, 0, 0, "Activity", 1),
                    (row_id, 1, 1, "Responsible role", 1),
                ],
            )
            section_id = connection.execute(
                """
                INSERT INTO document_sections
                    (source_id, ordinal, title, heading_block_id,
                     first_block_ordinal, last_block_ordinal)
                VALUES (?, 0, '1. Approval', ?, 0, 3)
                """,
                (source_id, heading_id),
            ).lastrowid
            synthetic_section_id = connection.execute(
                """
                INSERT INTO document_sections
                    (source_id, ordinal, title, parent_section_id, is_synthetic,
                     first_block_ordinal, last_block_ordinal, metadata_json)
                VALUES (?, 1, 'Unclassified remainder', ?, 1, 3, 3, '{}')
                """,
                (source_id, section_id),
            ).lastrowid
            chunk_id = connection.execute(
                "SELECT id FROM knowledge_chunks WHERE source_id = ? AND chunk_index = 0",
                (source_id,),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO knowledge_chunk_sections
                    (source_id, chunk_id, section_id, ordinal)
                VALUES (?, ?, ?, 0)
                """,
                (source_id, chunk_id, section_id),
            )
            connection.executemany(
                """
                INSERT INTO knowledge_chunk_blocks
                    (source_id, chunk_id, block_id, ordinal, start_offset, end_offset)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (source_id, chunk_id, heading_id, 0, 0, 12),
                    (source_id, chunk_id, paragraph_id, 1, 0, 27),
                ],
            )

            stored_record = connection.execute(
                "SELECT original_text FROM document_records WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            stored_cells = connection.execute(
                """
                SELECT text FROM document_block_cells
                WHERE block_id = ? ORDER BY cell_index
                """,
                (row_id,),
            ).fetchall()
            stored_sections = connection.execute(
                """
                SELECT id, title, is_synthetic FROM document_sections
                WHERE source_id = ? ORDER BY ordinal
                """,
                (source_id,),
            ).fetchall()

            self.assertEqual(stored_record["original_text"], raw_text)
            self.assertNotEqual(
                stored_record["original_text"], approved_source["original_text"]
            )
            self.assertEqual(
                [cell["text"] for cell in stored_cells],
                ["Activity", "Responsible role"],
            )
            self.assertEqual(stored_sections[0]["id"], section_id)
            self.assertEqual(stored_sections[1]["id"], synthetic_section_id)
            self.assertEqual(stored_sections[1]["is_synthetic"], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunk_blocks WHERE source_id = ?",
                    (source_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            connection.execute(
                "UPDATE document_blocks SET text = ? WHERE id = ?",
                ("Updated source paragraph.", paragraph_id),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT text FROM document_blocks WHERE id = ?", (paragraph_id,)
                ).fetchone()["text"],
                "Updated source paragraph.",
            )

        self.assertTrue(knowledge_store.delete_source(source_id))
        with knowledge_store._connection() as connection:
            for table in (
                "document_records",
                "document_blocks",
                "document_block_cells",
                "document_sections",
                "knowledge_chunk_sections",
                "knowledge_chunk_blocks",
                "knowledge_chunks",
            ):
                with self.subTest(table=table):
                    self.assertEqual(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE "
                            f"{'source_id' if table not in {'document_block_cells'} else 'block_id'} = ?",
                            (source_id if table != "document_block_cells" else row_id,),
                        ).fetchone()[0],
                        0,
                    )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_document_relations_reject_missing_or_cross_source_parents(self):
        knowledge_store.initialize_database()
        first = knowledge_store.save_text("First", "First source.", source_type="Docs")
        second = knowledge_store.save_text("Second", "Second source.", source_type="Docs")
        with knowledge_store._connection() as connection:
            connection.executemany(
                """
                INSERT INTO document_records
                    (source_id, original_text, created_at, updated_at)
                VALUES (?, ?, 'now', 'now')
                """,
                [(first["id"], "Raw first"), (second["id"], "Raw second")],
            )
            first_section_id = connection.execute(
                """
                INSERT INTO document_sections (source_id, ordinal, title)
                VALUES (?, 0, 'First section')
                """,
                (first["id"],),
            ).lastrowid
            first_chunk_id = connection.execute(
                "SELECT id FROM knowledge_chunks WHERE source_id = ?",
                (first["id"],),
            ).fetchone()["id"]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO knowledge_chunk_sections
                        (source_id, chunk_id, section_id)
                    VALUES (?, ?, ?)
                    """,
                    (second["id"], first_chunk_id, first_section_id),
                )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
