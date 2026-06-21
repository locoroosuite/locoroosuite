"""Versioned schema migrations for the docs cache database.

Previously the docs cache used a single ``_migrate_schema`` function with no
memoization (it ran on every ``open_cache`` call). This consolidates the
schema creation and column additions into the versioned runner.

Ordering note: ``idx_documents_folder`` references ``documents.folder_path``,
which was historically added by migration 0002. The index creation lives in
migration 0003 so it runs *after* the column is guaranteed to exist. Creating
it earlier would raise "no such column" on a pre-migration DB.
"""

from __future__ import annotations

from app.shared.migrations import Migration, table_columns


def _baseline_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            doc_type TEXT NOT NULL DEFAULT 'odt',
            original_format TEXT,
            file_size INTEGER NOT NULL DEFAULT 0,
            account_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT,
            folder_path TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(account_id, path)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_account ON documents(account_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_deleted ON documents(deleted_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_folders_account ON folders(account_id)"
    )


def _ensure_document_columns(conn) -> None:
    """Add historically-introduced columns to documents (old-DB upgrade path).

    The baseline CREATE TABLE already includes these for fresh DBs; this
    migration handles DBs created before the columns existed.
    """
    cols = table_columns(conn, "documents")
    if "original_format" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN original_format TEXT")
    if "folder_path" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''")
    if "tags" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")


def _ensure_document_folder_index(conn) -> None:
    """Create the folder_path index after the column is guaranteed to exist."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_folder ON documents(folder_path)")


DOCS_CACHE_MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001_baseline_schema", _baseline_schema),
    Migration("0002_ensure_document_columns", _ensure_document_columns),
    Migration("0003_ensure_document_folder_index", _ensure_document_folder_index),
)
