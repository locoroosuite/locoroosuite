"""Folder path validation, tree building, and higher-level folder operations.

Folders are a virtual organizational layer. The on-disk layout is unchanged;
folder membership lives on ``documents.folder_path`` and in the ``folders``
cache table (the latter only so empty folders persist within a session). Path
strings are slash-separated, relative to the account root, with no leading
slash. The root folder is the empty string ``""``.
"""
from __future__ import annotations

from typing import Any

MAX_DEPTH = 8
SEP = "/"
MAX_NAME_LEN = 100


class FolderError(ValueError):
    """Raised for invalid folder names or paths."""


def validate_folder_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise FolderError("Folder name is required.")
    if len(cleaned) > MAX_NAME_LEN:
        raise FolderError(f"Folder name must be {MAX_NAME_LEN} characters or fewer.")
    if "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise FolderError("Folder name may not contain '/', '\\', or null bytes.")
    return cleaned


def normalize_path(parent: str | None, name: str) -> str:
    """Build a full folder path from an optional parent and a leaf name."""
    leaf = validate_folder_name(name)
    parent = (parent or "").strip().strip(SEP)
    if not parent:
        return leaf
    return SEP.join(parent.split(SEP)) + SEP + leaf


def parent_path(path: str) -> str:
    path = (path or "").rstrip(SEP)
    if SEP not in path:
        return ""
    return path.rsplit(SEP, 1)[0]


def leaf_name(path: str) -> str:
    path = (path or "").rstrip(SEP)
    if not path:
        return ""
    return path.rsplit(SEP, 1)[-1]


def depth(path: str) -> int:
    path = (path or "").strip(SEP)
    if not path:
        return 0
    return len(path.split(SEP))


def assert_depth(path: str) -> None:
    d = depth(path)
    if d > MAX_DEPTH:
        raise FolderError(f"Folder nesting exceeds the maximum depth of {MAX_DEPTH}.")


def ancestors(path: str) -> list[str]:
    """Return ancestor paths from shallowest to deepest, excluding the path itself."""
    path = (path or "").strip(SEP)
    if not path:
        return []
    parts = path.split(SEP)
    result = []
    for i in range(1, len(parts)):
        result.append(SEP.join(parts[:i]))
    return result


def ensure_folder_path(conn, account_id, path: str) -> None:
    """Create the folder row for ``path`` and any missing ancestor segments."""
    path = (path or "").strip(SEP)
    if not path:
        return
    assert_depth(path)
    from app.modules.docs.services import cache_db
    parts = path.split(SEP)
    cumulative = ""
    for part in parts:
        cumulative = part if not cumulative else cumulative + SEP + part
        leaf = validate_folder_name(part)
        if not cache_db.folder_exists(conn, account_id, cumulative):
            cache_db.create_folder(conn, account_id, cumulative, leaf)


def build_tree(folder_rows: list[dict], doc_folder_paths: list[str]) -> list[dict]:
    """Build a nested folder tree from folder rows + distinct doc folder paths.

    Each node: ``{name, path, count, children: [...]}`` where ``count`` is the
    number of documents directly in that folder (exact folder_path match).
    Folders present in the table but containing no documents still appear (so
    empty folders are visible). Paths inferred only from documents (no row) are
    also materialized as nodes.
    """
    counts: dict[str, int] = {}
    for p in doc_folder_paths:
        counts[p] = counts.get(p, 0) + 1

    known_paths = {r["path"] for r in folder_rows}
    all_paths = set(known_paths) | set(counts.keys())

    nodes_by_path: dict[str, Any] = {}
    roots: list[dict] = []
    for p in sorted(all_paths, key=lambda x: (x.count(SEP), x.lower())):
        parts = p.split(SEP)
        node = {
            "name": parts[-1],
            "path": p,
            "count": counts.get(p, 0),
            "children": [],
        }
        nodes_by_path[p] = node
        parent = SEP.join(parts[:-1])
        if parent and parent in nodes_by_path:
            nodes_by_path[parent]["children"].append(node)
        elif parent:
            # Parent path missing from our sorted set (shouldn't happen since we
            # walk shallow-first), attach to roots as fallback.
            roots.append(node)
        else:
            roots.append(node)
    return roots


def list_tree(conn, account_id) -> list[dict]:
    """Convenience: read folders + doc paths and return the full tree."""
    from app.modules.docs.services import cache_db
    folder_rows = cache_db.list_folders(conn, account_id)
    doc_paths = cache_db.distinct_doc_folder_paths(conn, account_id)
    return build_tree(folder_rows, doc_paths)


def list_flat(conn, account_id) -> list[dict]:
    """Return a flat list of folders (rows + paths inferred from documents).

    Each item: ``{path, name, parent, count}`` where ``count`` is the number of
    active documents directly in that folder. API-friendly alternative to the
    nested :func:`list_tree`.
    """
    from app.modules.docs.services import cache_db
    folder_rows = cache_db.list_folders(conn, account_id)
    doc_paths = cache_db.distinct_doc_folder_paths(conn, account_id)
    counts: dict[str, int] = {}
    for p in doc_paths:
        counts[p] = counts.get(p, 0) + 1
    all_paths = set(r["path"] for r in folder_rows) | set(counts.keys())
    result = []
    for p in sorted(all_paths, key=lambda x: (x.count(SEP), x.lower())):
        parts = p.split(SEP)
        result.append({
            "path": p,
            "name": parts[-1],
            "parent": SEP.join(parts[:-1]),
            "count": counts.get(p, 0),
        })
    return result
