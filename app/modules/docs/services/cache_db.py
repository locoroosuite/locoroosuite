import json
import os
import uuid

import sqlcipher3

from app.modules.docs.services.cache_migrations import DOCS_CACHE_MIGRATIONS
from app.shared.cache_errors import CacheKeyMismatchError
from app.shared.migrations import run_migrations


# Cache DB paths that have already had their schema initialized/migrated in
# this process. open_cache() is called ~per-request, but the schema check only
# needs to run once per process per cache file. Mirrors the mail cache pattern.
_SCHEMA_INITIALIZED: set[str] = set()


def clear_cache_schema_memo(db_path: str | None) -> None:
    if db_path:
        _SCHEMA_INITIALIZED.discard(db_path)


def open_cache(db_path, key):
    if not key:
        raise ValueError("cache key required")
    file_existed = bool(db_path) and os.path.exists(db_path)
    memo_valid = db_path in _SCHEMA_INITIALIZED and file_existed
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    try:
        if not memo_valid:
            _init_schema(conn)
            _SCHEMA_INITIALIZED.add(db_path)
    except (MemoryError, Exception) as exc:
        conn.close()
        if os.path.exists(db_path):
            os.unlink(db_path)
            clear_cache_schema_memo(db_path)
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        try:
            _init_schema(conn)
            _SCHEMA_INITIALIZED.add(db_path)
        except Exception:
            conn.close()
            raise CacheKeyMismatchError(
                f"Failed to open cache database even after reset. db_path={db_path}"
            ) from exc
    return conn


def _init_schema(conn):
    run_migrations(conn, DOCS_CACHE_MIGRATIONS)


def parse_tags(raw):
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(t) for t in v] if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def create_document(conn, doc_id, name, doc_type, account_id, file_size=0, original_format=None, folder_path="", tags=None):
    conn.execute(
        "INSERT INTO documents (id, name, doc_type, original_format, file_size, account_id, folder_path, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, name, doc_type, original_format, file_size, account_id, folder_path, json.dumps(tags or [])),
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


def list_documents(conn, account_id, include_trash=False, folder=None, tag=None):
    query = "SELECT * FROM documents WHERE account_id = ?"
    params = [account_id]
    if not include_trash:
        query += " AND deleted_at IS NULL"
    if folder is not None:
        query += " AND folder_path = ?"
        params.append(folder)
    query += " ORDER BY updated_at DESC"
    rows = conn.execute(query, params).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM documents LIMIT 0").description]
    docs = [dict(zip(cols, r)) for r in rows]
    if tag:
        docs = [d for d in docs if tag in parse_tags(d.get("tags"))]
    return docs


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


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def get_document_tags(conn, doc_id):
    row = conn.execute("SELECT tags FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return parse_tags(row["tags"]) if row else []


def set_document_tags(conn, doc_id, tags):
    conn.execute(
        "UPDATE documents SET tags = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(tags), doc_id),
    )
    conn.commit()


def update_document_tags(conn, doc_id, add=None, remove=None):
    current = get_document_tags(conn, doc_id)
    add_set = list(dict.fromkeys(add or []))
    remove_set = set(remove or [])
    merged = [t for t in current if t not in remove_set]
    for t in add_set:
        if t not in merged:
            merged.append(t)
    set_document_tags(conn, doc_id, merged)
    return merged


def list_all_tags(conn, account_id):
    rows = conn.execute(
        "SELECT DISTINCT tags FROM documents WHERE account_id = ? AND deleted_at IS NULL",
        (account_id,),
    ).fetchall()
    seen = []
    for r in rows:
        for t in parse_tags(r["tags"]):
            if t not in seen:
                seen.append(t)
    return sorted(seen, key=str.lower)


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def create_folder(conn, account_id, path, name):
    folder_id = uuid.uuid4().hex
    conn.execute(
        "INSERT OR IGNORE INTO folders (id, account_id, path, name) VALUES (?, ?, ?, ?)",
        (folder_id, account_id, path, name),
    )
    conn.commit()
    return folder_id


def get_folder_by_path(conn, account_id, path):
    row = conn.execute(
        "SELECT * FROM folders WHERE account_id = ? AND path = ?",
        (account_id, path),
    ).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM folders LIMIT 0").description]
    return dict(zip(cols, row))


def folder_exists(conn, account_id, path):
    return get_folder_by_path(conn, account_id, path) is not None


def list_folders(conn, account_id):
    rows = conn.execute(
        "SELECT * FROM folders WHERE account_id = ? ORDER BY path",
        (account_id,),
    ).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM folders LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def delete_folder_subtree_rows(conn, account_id, path):
    like = path + "/%"
    conn.execute(
        "DELETE FROM folders WHERE account_id = ? AND (path = ? OR path LIKE ?)",
        (account_id, path, like),
    )
    conn.commit()


def rename_folder_subtree(conn, account_id, old_prefix, new_prefix):
    """Rename a folder and all descendants, rewriting document folder paths.

    Computes new paths in Python to avoid SQL REPLACE edge cases (a segment
    name being a substring of another). All rows whose path equals ``old_prefix``
    or sits beneath it are rewritten to the corresponding ``new_prefix`` path.
    """
    like = old_prefix + "/%"
    folder_rows = conn.execute(
        "SELECT id, path FROM folders WHERE account_id = ? AND (path = ? OR path LIKE ?)",
        (account_id, old_prefix, like),
    ).fetchall()
    for r in folder_rows:
        old_path = r["path"]
        new_path = new_prefix + old_path[len(old_prefix):]
        leaf = new_path.rsplit("/", 1)[-1]
        conn.execute(
            "UPDATE folders SET path = ?, name = ? WHERE id = ?",
            (new_path, leaf, r["id"]),
        )
    doc_rows = conn.execute(
        "SELECT id, folder_path FROM documents WHERE account_id = ? AND (folder_path = ? OR folder_path LIKE ?)",
        (account_id, old_prefix, like),
    ).fetchall()
    for r in doc_rows:
        old_path = r["folder_path"]
        new_path = new_prefix + old_path[len(old_prefix):]
        conn.execute(
            "UPDATE documents SET folder_path = ?, updated_at = datetime('now') WHERE id = ?",
            (new_path, r["id"]),
        )
    conn.commit()


def move_subtree_docs_to_parent(conn, account_id, deleted_path, parent_path):
    """On folder delete: flatten all documents in the subtree to ``parent_path``."""
    like = deleted_path + "/%"
    conn.execute(
        "UPDATE documents SET folder_path = ?, updated_at = datetime('now') "
        "WHERE account_id = ? AND (folder_path = ? OR folder_path LIKE ?)",
        (parent_path, account_id, deleted_path, like),
    )
    conn.commit()


def subtree_documents(conn, account_id, path):
    """Return all documents (active + trashed) whose folder is ``path`` or beneath it."""
    like = path + "/%"
    rows = conn.execute(
        "SELECT * FROM documents WHERE account_id = ? AND (folder_path = ? OR folder_path LIKE ?)",
        (account_id, path, like),
    ).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM documents LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def set_document_folder(conn, doc_id, folder_path):
    conn.execute(
        "UPDATE documents SET folder_path = ?, updated_at = datetime('now') WHERE id = ?",
        (folder_path, doc_id),
    )
    conn.commit()


def distinct_doc_folder_paths(conn, account_id):
    rows = conn.execute(
        "SELECT DISTINCT folder_path FROM documents "
        "WHERE account_id = ? AND deleted_at IS NULL AND folder_path != ''",
        (account_id,),
    ).fetchall()
    return [r[0] for r in rows]
