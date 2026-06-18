import io
import json
import os
import shutil
import tempfile

import pytest

from app.modules.docs.services import cache_db, doc_meta, resync as resync_svc
from app.modules.docs.services.templates import empty_odt, empty_ods
from app.modules.docs.services import storage

_seq = 0


def _next_ids():
    global _seq
    _seq += 1
    return 90000 + _seq, 80000 + _seq


@pytest.fixture
def cache_conn(app):
    user_id, account_id = _next_ids()
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = f.name
    f.close()
    key = "0" * 64
    conn = cache_db.open_cache(path, key)
    ctx = app.app_context()
    ctx.push()
    yield conn, user_id, account_id
    ctx.pop()
    conn.close()
    os.unlink(path)
    doc_dir = storage.get_docs_dir() / str(user_id) / str(account_id)
    if doc_dir.exists():
        shutil.rmtree(doc_dir, ignore_errors=True)
    parent = storage.get_docs_dir() / str(user_id)
    if parent.exists():
        try:
            shutil.rmtree(parent, ignore_errors=True)
        except OSError:
            pass


def _make_odt_with_meta(doc_id, name, doc_type="odt", account_id=1, deleted_at=None):
    template = empty_odt().read()
    metadata = resync_svc.build_doc_metadata(doc_id, name, doc_type, account_id, deleted_at=deleted_at)
    return doc_meta.inject_metadata(template, metadata)


def _make_ods_with_meta(doc_id, name, account_id=1):
    template = empty_ods().read()
    metadata = resync_svc.build_doc_metadata(doc_id, name, "ods", account_id)
    return doc_meta.inject_metadata(template, metadata)


class TestResyncDocs:
    def test_resync_recovers_from_metadata(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "abc123def"
        file_data = _make_odt_with_meta(doc_id, "Report", account_id=account_id)
        storage.write_file(user_id, account_id, doc_id, file_data)

        count = resync_svc.resync_docs(conn, user_id, account_id)
        assert count == 1

        doc = cache_db.get_document(conn, doc_id)
        assert doc is not None
        assert doc["name"] == "Report"
        assert doc["doc_type"] == "odt"
        assert doc["account_id"] == account_id
        assert doc["deleted_at"] is None

    def test_resync_recovers_trashed_documents(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "trashed001"
        file_data = _make_odt_with_meta(
            doc_id, "Deleted Doc", account_id=account_id, deleted_at="2026-05-20 08:00:00"
        )
        storage.write_file(user_id, account_id, doc_id, file_data)

        count = resync_svc.resync_docs(conn, user_id, account_id)
        assert count == 1

        doc = cache_db.get_document(conn, doc_id)
        assert doc is not None
        assert doc["deleted_at"] is not None

    def test_resync_skips_existing_documents(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "existing01"
        cache_db.create_document(conn, doc_id, "Old Name", "odt", account_id)

        file_data = _make_odt_with_meta(doc_id, "New Name", account_id=account_id)
        storage.write_file(user_id, account_id, doc_id, file_data)

        count = resync_svc.resync_docs(conn, user_id, account_id)
        assert count == 0

        doc = cache_db.get_document(conn, doc_id)
        assert doc["name"] == "Old Name"

    def test_resync_handles_no_metadata(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "nometa00001"
        template = empty_odt().read()
        storage.write_file(user_id, account_id, doc_id, template)

        count = resync_svc.resync_docs(conn, user_id, account_id)
        assert count == 1

        doc = cache_db.get_document(conn, doc_id)
        assert doc is not None
        assert doc["name"] == doc_id[:8]
        assert doc["doc_type"] == "odt"

    def test_resync_handles_empty_dir(self, cache_conn):
        conn, user_id, account_id = cache_conn
        count = resync_svc.resync_docs(conn, user_id, account_id)
        assert count == 0

    def test_resync_handles_nonexistent_dir(self, cache_conn):
        conn, user_id, account_id = cache_conn
        count = resync_svc.resync_docs(conn, 99999, 88888)
        assert count == 0

    def test_resync_multiple_documents(self, cache_conn):
        conn, user_id, account_id = cache_conn
        for i in range(5):
            doc_id = f"doc_{i:08d}"
            file_data = _make_odt_with_meta(doc_id, f"Doc {i}", account_id=account_id)
            storage.write_file(user_id, account_id, doc_id, file_data)

        count = resync_svc.resync_docs(conn, user_id, account_id)
        assert count == 5

        docs = cache_db.list_documents(conn, account_id)
        assert len(docs) == 5

    def test_resync_recovers_file_size(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "sizetest01"
        file_data = _make_odt_with_meta(doc_id, "Big Doc", account_id=account_id)
        storage.write_file(user_id, account_id, doc_id, file_data)

        resync_svc.resync_docs(conn, user_id, account_id)

        doc = cache_db.get_document(conn, doc_id)
        assert doc["file_size"] == len(file_data)

    def test_resync_preserves_created_at(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "date000001"
        metadata = resync_svc.build_doc_metadata(
            doc_id, "DateDoc", "odt", account_id,
            created_at="2026-01-10 09:00:00", updated_at="2026-02-15 14:30:00",
        )
        template = empty_odt().read()
        file_data = doc_meta.inject_metadata(template, metadata)
        storage.write_file(user_id, account_id, doc_id, file_data)

        resync_svc.resync_docs(conn, user_id, account_id)

        doc = cache_db.get_document(conn, doc_id)
        assert doc["created_at"] == "2026-01-10 09:00:00"
        assert doc["updated_at"] == "2026-02-15 14:30:00"

    def test_resync_guesses_ods_type(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "guess_ods01"
        file_data = _make_ods_with_meta(doc_id, "Sheet", account_id=account_id)
        storage.write_file(user_id, account_id, doc_id, file_data)

        resync_svc.resync_docs(conn, user_id, account_id)

        doc = cache_db.get_document(conn, doc_id)
        assert doc["doc_type"] == "ods"

    def test_resync_guesses_pdf_as_drawing(self, cache_conn):
        # A bare PDF (no metadata/sidecar) must recover as an editable drawing
        # (odg), mirroring the convert target. Guessing odt would make the later
        # "Convert" action request an impossible pdf->odt conversion.
        conn, user_id, account_id = cache_conn
        doc_id = "guess_pdf01"
        storage.write_file(user_id, account_id, doc_id, b"%PDF-1.4 fake pdf body")

        resync_svc.resync_docs(conn, user_id, account_id)

        doc = cache_db.get_document(conn, doc_id)
        assert doc is not None
        assert doc["doc_type"] == "odg"
        assert doc["original_format"] == "pdf"

    def test_resync_skips_dirs_without_content(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_dir = storage.get_docs_dir() / str(user_id) / str(account_id) / "emptydoc00"
        doc_dir.mkdir(parents=True, exist_ok=True)

        count = resync_svc.resync_docs(conn, user_id, account_id)
        assert count == 0


class TestBuildDocMetadata:
    def test_basic_metadata(self):
        m = resync_svc.build_doc_metadata("id1", "Name", "odt", 42)
        assert m == {
            "id": "id1",
            "name": "Name",
            "doc_type": "odt",
            "original_format": None,
            "account_id": 42,
            "deleted_at": None,
            "created_at": None,
            "updated_at": None,
        }

    def test_with_all_fields(self):
        m = resync_svc.build_doc_metadata(
            "id2", "Full", "ods", 10,
            original_format="xlsx",
            deleted_at="2024-01-01",
            created_at="2024-01-02",
            updated_at="2024-01-03",
        )
        assert m == {
            "id": "id2",
            "name": "Full",
            "doc_type": "ods",
            "original_format": "xlsx",
            "account_id": 10,
            "deleted_at": "2024-01-01",
            "created_at": "2024-01-02",
            "updated_at": "2024-01-03",
        }


class TestInjectMetadataFromDocRow:
    def test_injects_from_dict(self, cache_conn):
        conn, user_id, account_id = cache_conn
        doc_id = "rowtest001"
        template = empty_odt().read()
        storage.write_file(user_id, account_id, doc_id, template)

        doc = {
            "id": doc_id, "name": "Row Doc", "doc_type": "odt",
            "account_id": account_id, "deleted_at": None,
            "created_at": None, "updated_at": None,
        }
        resync_svc.inject_metadata_from_doc_row(user_id, account_id, doc)

        file_bytes = storage.read_file(user_id, account_id, doc_id)
        extracted = doc_meta.extract_metadata(file_bytes)
        assert extracted["name"] == "Row Doc"
        assert extracted["id"] == doc_id
