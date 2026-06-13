import sqlcipher3

from app.shared.cache_errors import CacheKeyMismatchError


def open_cache(db_path, key):
    if not key:
        raise ValueError("cache key required")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    try:
        _init_schema(conn)
    except (MemoryError, Exception) as exc:
        conn.close()
        import os as _os
        if _os.path.exists(db_path):
            _os.unlink(db_path)
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        try:
            _init_schema(conn)
        except Exception:
            conn.close()
            raise CacheKeyMismatchError(
                f"Failed to open cache database even after reset. db_path={db_path}"
            ) from exc
    return conn


def _init_schema(conn):
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
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_account ON documents(account_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_deleted ON documents(deleted_at)"
    )
    _migrate_schema(conn)
    conn.commit()


def _migrate_schema(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
    if "original_format" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN original_format TEXT")


def create_document(conn, doc_id, name, doc_type, account_id, file_size=0, original_format=None):
    conn.execute(
        "INSERT INTO documents (id, name, doc_type, original_format, file_size, account_id) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, name, doc_type, original_format, file_size, account_id),
    )
    conn.commit()


def get_document(conn, doc_id):
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM documents LIMIT 0").description]
    return dict(zip(cols, row))


def get_active_document(conn, doc_id):
    row = conn.execute("SELECT * FROM documents WHERE id = ? AND deleted_at IS NULL", (doc_id,)).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM documents LIMIT 0").description]
    return dict(zip(cols, row))


def list_documents(conn, account_id, include_trash=False):
    if include_trash:
        rows = conn.execute(
            "SELECT * FROM documents WHERE account_id = ? ORDER BY updated_at DESC",
            (account_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM documents WHERE account_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC",
            (account_id,),
        ).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM documents LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def list_trash(conn, account_id):
    rows = conn.execute(
        "SELECT * FROM documents WHERE account_id = ? AND deleted_at IS NOT NULL ORDER BY updated_at DESC",
        (account_id,),
    ).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM documents LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def rename_document(conn, doc_id, name):
    conn.execute(
        "UPDATE documents SET name = ?, updated_at = datetime('now') WHERE id = ?",
        (name, doc_id),
    )
    conn.commit()


def soft_delete_document(conn, doc_id):
    conn.execute(
        "UPDATE documents SET deleted_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (doc_id,),
    )
    conn.commit()


def restore_document(conn, doc_id):
    conn.execute(
        "UPDATE documents SET deleted_at = NULL, updated_at = datetime('now') WHERE id = ?",
        (doc_id,),
    )
    conn.commit()


def hard_delete_document(conn, doc_id):
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()


def update_file_size(conn, doc_id, file_size):
    conn.execute(
        "UPDATE documents SET file_size = ?, updated_at = datetime('now') WHERE id = ?",
        (file_size, doc_id),
    )
    conn.commit()


def count_documents(conn, account_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE account_id = ? AND deleted_at IS NULL",
        (account_id,),
    ).fetchone()
    return row[0] if row else 0
