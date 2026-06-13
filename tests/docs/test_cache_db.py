import os
import tempfile

import pytest

from app.modules.docs.services.cache_db import open_cache


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
    from app.modules.docs.services.cache_db import create_document, get_document
    create_document(cache_conn, "doc-1", "Test Doc", "odt", 1)
    doc = get_document(cache_conn, "doc-1")
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
    from app.modules.docs.services.cache_db import create_document, rename_document, get_document
    create_document(cache_conn, "doc-1", "Old Name", "odt", 1)
    rename_document(cache_conn, "doc-1", "New Name")
    doc = get_document(cache_conn, "doc-1")
    assert doc["name"] == "New Name"


def test_soft_delete_and_restore(cache_conn):
    from app.modules.docs.services.cache_db import create_document, soft_delete_document, restore_document, get_document
    create_document(cache_conn, "doc-1", "Doc", "odt", 1)
    soft_delete_document(cache_conn, "doc-1")
    doc = get_document(cache_conn, "doc-1")
    assert doc["deleted_at"] is not None

    restore_document(cache_conn, "doc-1")
    doc = get_document(cache_conn, "doc-1")
    assert doc["deleted_at"] is None


def test_hard_delete_document(cache_conn):
    from app.modules.docs.services.cache_db import create_document, hard_delete_document, get_document
    create_document(cache_conn, "doc-1", "Doc", "odt", 1)
    hard_delete_document(cache_conn, "doc-1")
    assert get_document(cache_conn, "doc-1") is None


def test_update_file_size(cache_conn):
    from app.modules.docs.services.cache_db import create_document, update_file_size, get_document
    create_document(cache_conn, "doc-1", "Doc", "odt", 1)
    update_file_size(cache_conn, "doc-1", 4096)
    doc = get_document(cache_conn, "doc-1")
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
