import os
import tempfile

import pytest

from app.modules.docs.services.cache_db import open_cache


def _doc(conn, doc_id):
    from app.modules.docs.services.cache_db import get_document
    doc = get_document(conn, doc_id)
    assert doc is not None, f"document {doc_id} not found"
    return doc


@pytest.fixture
def cache_conn():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = f.name
    f.close()
    key = "0" * 64
    conn = open_cache(path, key)
    yield conn
    conn.close()
    os.unlink(path)


def test_create_and_get_document(cache_conn):
    from app.modules.docs.services.cache_db import create_document
    create_document(cache_conn, "doc-1", "Test Doc", "odt", 1)
    doc = _doc(cache_conn, "doc-1")
    assert doc is not None
    assert doc["name"] == "Test Doc"
    assert doc["doc_type"] == "odt"
    assert doc["account_id"] == 1
    assert doc["deleted_at"] is None


def test_get_document_nonexistent(cache_conn):
    from app.modules.docs.services.cache_db import get_document
    assert get_document(cache_conn, "nope") is None


def test_list_documents(cache_conn):
    from app.modules.docs.services.cache_db import create_document, list_documents
    create_document(cache_conn, "doc-1", "Doc A", "odt", 1)
    create_document(cache_conn, "doc-2", "Doc B", "ods", 1)
    docs = list_documents(cache_conn, 1)
    assert len(docs) == 2


def test_list_documents_excludes_trash(cache_conn):
    from app.modules.docs.services.cache_db import create_document, soft_delete_document, list_documents
    create_document(cache_conn, "doc-1", "Doc A", "odt", 1)
    create_document(cache_conn, "doc-2", "Doc B", "ods", 1)
    soft_delete_document(cache_conn, "doc-1")
    docs = list_documents(cache_conn, 1)
    assert len(docs) == 1
    assert docs[0]["id"] == "doc-2"


def test_list_documents_with_trash(cache_conn):
    from app.modules.docs.services.cache_db import create_document, soft_delete_document, list_documents
    create_document(cache_conn, "doc-1", "Doc A", "odt", 1)
    soft_delete_document(cache_conn, "doc-1")
    docs = list_documents(cache_conn, 1, include_trash=True)
    assert len(docs) == 1


def test_list_trash(cache_conn):
    from app.modules.docs.services.cache_db import create_document, soft_delete_document, list_trash
    create_document(cache_conn, "doc-1", "Doc A", "odt", 1)
    create_document(cache_conn, "doc-2", "Doc B", "ods", 1)
    soft_delete_document(cache_conn, "doc-1")
    trash = list_trash(cache_conn, 1)
    assert len(trash) == 1
    assert trash[0]["id"] == "doc-1"


def test_rename_document(cache_conn):
    from app.modules.docs.services.cache_db import create_document, rename_document
    create_document(cache_conn, "doc-1", "Old Name", "odt", 1)
    rename_document(cache_conn, "doc-1", "New Name")
    doc = _doc(cache_conn, "doc-1")
    assert doc["name"] == "New Name"


def test_soft_delete_and_restore(cache_conn):
    from app.modules.docs.services.cache_db import create_document, soft_delete_document, restore_document
    create_document(cache_conn, "doc-1", "Doc", "odt", 1)
    soft_delete_document(cache_conn, "doc-1")
    doc = _doc(cache_conn, "doc-1")
    assert doc["deleted_at"] is not None

    restore_document(cache_conn, "doc-1")
    doc = _doc(cache_conn, "doc-1")
    assert doc["deleted_at"] is None


def test_hard_delete_document(cache_conn):
    from app.modules.docs.services.cache_db import create_document, hard_delete_document, get_document
    create_document(cache_conn, "doc-1", "Doc", "odt", 1)
    hard_delete_document(cache_conn, "doc-1")
    assert get_document(cache_conn, "doc-1") is None


def test_update_file_size(cache_conn):
    from app.modules.docs.services.cache_db import create_document, update_file_size
    create_document(cache_conn, "doc-1", "Doc", "odt", 1)
    update_file_size(cache_conn, "doc-1", 4096)
    doc = _doc(cache_conn, "doc-1")
    assert doc["file_size"] == 4096


def test_count_documents(cache_conn):
    from app.modules.docs.services.cache_db import create_document, soft_delete_document, count_documents
    assert count_documents(cache_conn, 1) == 0
    create_document(cache_conn, "doc-1", "A", "odt", 1)
    create_document(cache_conn, "doc-2", "B", "ods", 1)
    assert count_documents(cache_conn, 1) == 2
    soft_delete_document(cache_conn, "doc-1")
    assert count_documents(cache_conn, 1) == 1


def test_list_documents_filters_by_account(cache_conn):
    from app.modules.docs.services.cache_db import create_document, list_documents
    create_document(cache_conn, "doc-1", "A", "odt", 1)
    create_document(cache_conn, "doc-2", "B", "odt", 2)
    docs = list_documents(cache_conn, 1)
    assert len(docs) == 1
    assert docs[0]["id"] == "doc-1"


# ---------------------------------------------------------------------------
# Folders + tags (U13.90 / U13.91)
# ---------------------------------------------------------------------------

def test_migration_preserves_existing_data(tmp_path):
    # Regression: an existing pre-folders cache DB must migrate in place,
    # adding folder_path/tags + the folders table WITHOUT wiping documents.
    # (Creating the folder_path index before the column exists previously
    # triggered open_cache's corrupt-cache path and deleted everything.)
    import sqlcipher3
    from app.modules.docs.services import cache_db

    path = str(tmp_path / "old.db")
    key = "0" * 64
    raw = sqlcipher3.connect(path)
    raw.execute(f"PRAGMA key = \"x'{key}'\"")
    raw.execute(
        "CREATE TABLE documents (id TEXT PRIMARY KEY, name TEXT, doc_type TEXT, "
        "original_format TEXT, file_size INTEGER, account_id INTEGER, "
        "created_at TEXT, updated_at TEXT, deleted_at TEXT)"
    )
    raw.execute("INSERT INTO documents (id, name, doc_type, account_id) VALUES ('d1', 'Old', 'odt', 1)")
    raw.commit()
    raw.close()

    conn = cache_db.open_cache(path, key)
    try:
        rows = conn.execute("SELECT id, name, folder_path, tags FROM documents").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == "d1"
        assert rows[0]["folder_path"] == ""
        assert rows[0]["tags"] == "[]"
        # folders table created and empty.
        assert conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0
    finally:
        conn.close()


def test_create_document_defaults_folder_and_tags(cache_conn):
    from app.modules.docs.services.cache_db import create_document
    create_document(cache_conn, "d1", "Doc", "odt", 1)
    doc = _doc(cache_conn, "d1")
    assert doc["folder_path"] == ""
    assert doc["tags"] == "[]"


def test_create_document_with_folder_and_tags(cache_conn):
    from app.modules.docs.services.cache_db import create_document
    create_document(cache_conn, "d1", "Doc", "odt", 1, folder_path="Work/A", tags=["x", "y"])
    doc = _doc(cache_conn, "d1")
    assert doc["folder_path"] == "Work/A"
    assert doc["tags"] == '["x", "y"]'


def test_list_documents_folder_filter_is_exact(cache_conn):
    from app.modules.docs.services.cache_db import create_document, list_documents
    create_document(cache_conn, "root", "Root", "odt", 1)
    create_document(cache_conn, "a", "A", "odt", 1, folder_path="Work")
    create_document(cache_conn, "b", "B", "odt", 1, folder_path="Work/Sub")
    # Exact match on "Work" excludes the nested "Work/Sub" doc.
    docs = list_documents(cache_conn, 1, folder="Work")
    assert [d["id"] for d in docs] == ["a"]


def test_list_documents_tag_filter(cache_conn):
    from app.modules.docs.services.cache_db import create_document, list_documents
    create_document(cache_conn, "a", "A", "odt", 1, tags=["urgent"])
    create_document(cache_conn, "b", "B", "odt", 1, tags=["finance"])
    create_document(cache_conn, "c", "C", "odt", 1, tags=["urgent", "finance"])
    urgent = list_documents(cache_conn, 1, tag="urgent")
    assert sorted(d["id"] for d in urgent) == ["a", "c"]


def test_tags_add_remove_set(cache_conn):
    from app.modules.docs.services.cache_db import create_document, get_document_tags, update_document_tags, set_document_tags
    create_document(cache_conn, "d1", "Doc", "odt", 1, tags=["a"])
    update_document_tags(cache_conn, "d1", add=["b", "a"], remove=[])
    assert get_document_tags(cache_conn, "d1") == ["a", "b"]  # de-dup, order preserved
    update_document_tags(cache_conn, "d1", add=["c"], remove=["a"])
    assert get_document_tags(cache_conn, "d1") == ["b", "c"]
    set_document_tags(cache_conn, "d1", ["only"])
    assert get_document_tags(cache_conn, "d1") == ["only"]


def test_list_all_tags(cache_conn):
    from app.modules.docs.services.cache_db import create_document, list_all_tags, soft_delete_document
    create_document(cache_conn, "a", "A", "odt", 1, tags=["Zeta", "alpha"])
    create_document(cache_conn, "b", "B", "odt", 1, tags=["alpha"])
    # Sorted case-insensitively, de-duplicated.
    assert list_all_tags(cache_conn, 1) == ["alpha", "Zeta"]
    soft_delete_document(cache_conn, "a")
    # Trashed docs excluded.
    assert list_all_tags(cache_conn, 1) == ["alpha"]


def test_folder_crud_and_tree_inputs(cache_conn):
    from app.modules.docs.services.cache_db import create_folder, get_folder_by_path, folder_exists, list_folders
    create_folder(cache_conn, 1, "Work", "Work")
    assert folder_exists(cache_conn, 1, "Work")
    folder = get_folder_by_path(cache_conn, 1, "Work")
    assert folder is not None
    assert folder["name"] == "Work"
    assert not folder_exists(cache_conn, 1, "Nope")
    create_folder(cache_conn, 1, "Work/Projects", "Projects")
    rows = list_folders(cache_conn, 1)
    assert sorted(r["path"] for r in rows) == ["Work", "Work/Projects"]


def test_rename_folder_subtree_rewrites_docs_and_subfolders(cache_conn):
    from app.modules.docs.services.cache_db import (
        create_document, create_folder, rename_folder_subtree, list_folders,
    )
    create_folder(cache_conn, 1, "Old", "Old")
    create_folder(cache_conn, 1, "Old/Sub", "Sub")
    create_document(cache_conn, "d1", "A", "odt", 1, folder_path="Old")
    create_document(cache_conn, "d2", "B", "odt", 1, folder_path="Old/Sub")
    create_document(cache_conn, "d3", "C", "odt", 1, folder_path="Other")

    rename_folder_subtree(cache_conn, 1, "Old", "New")

    assert _doc(cache_conn, "d1")["folder_path"] == "New"
    assert _doc(cache_conn, "d2")["folder_path"] == "New/Sub"
    assert _doc(cache_conn, "d3")["folder_path"] == "Other"
    paths = sorted(r["path"] for r in list_folders(cache_conn, 1))
    assert paths == ["New", "New/Sub"]


def test_rename_folder_subtree_only_affects_segment_prefix(cache_conn):
    # Renaming "Old" must not rewrite a sibling like "Older".
    from app.modules.docs.services.cache_db import create_document, rename_folder_subtree
    create_document(cache_conn, "d1", "A", "odt", 1, folder_path="Old")
    create_document(cache_conn, "d2", "B", "odt", 1, folder_path="Older")
    rename_folder_subtree(cache_conn, 1, "Old", "New")
    assert _doc(cache_conn, "d1")["folder_path"] == "New"
    assert _doc(cache_conn, "d2")["folder_path"] == "Older"


def test_delete_folder_flattens_docs_to_parent(cache_conn):
    from app.modules.docs.services.cache_db import (
        create_document, create_folder, delete_folder_subtree_rows,
        move_subtree_docs_to_parent, list_folders,
    )
    create_folder(cache_conn, 1, "A", "A")
    create_folder(cache_conn, 1, "A/B", "B")
    create_document(cache_conn, "d1", "1", "odt", 1, folder_path="A/B")
    create_document(cache_conn, "d2", "2", "odt", 1, folder_path="A/B/C")

    move_subtree_docs_to_parent(cache_conn, 1, "A/B", "A")
    delete_folder_subtree_rows(cache_conn, 1, "A/B")

    assert _doc(cache_conn, "d1")["folder_path"] == "A"
    assert _doc(cache_conn, "d2")["folder_path"] == "A"
    assert all(not r["path"].startswith("A/B") for r in list_folders(cache_conn, 1))


def test_distinct_doc_folder_paths_excludes_trashed_and_root(cache_conn):
    from app.modules.docs.services.cache_db import (
        create_document, distinct_doc_folder_paths, soft_delete_document,
    )
    create_document(cache_conn, "a", "A", "odt", 1, folder_path="Work")
    create_document(cache_conn, "b", "B", "odt", 1, folder_path="Work")
    create_document(cache_conn, "c", "C", "odt", 1)  # root, excluded
    create_document(cache_conn, "d", "D", "odt", 1, folder_path="Other")
    soft_delete_document(cache_conn, "d")
    assert distinct_doc_folder_paths(cache_conn, 1) == ["Work"]


def test_subtree_documents_returns_nested(cache_conn):
    from app.modules.docs.services.cache_db import create_document, subtree_documents
    create_document(cache_conn, "a", "A", "odt", 1, folder_path="Work")
    create_document(cache_conn, "b", "B", "odt", 1, folder_path="Work/Sub")
    create_document(cache_conn, "c", "C", "odt", 1, folder_path="Other")
    ids = sorted(d["id"] for d in subtree_documents(cache_conn, 1, "Work"))
    assert ids == ["a", "b"]
