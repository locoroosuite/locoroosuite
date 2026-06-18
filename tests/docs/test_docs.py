import io
import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from app.modules.docs.services import collabora


def _setup_test_env(app, account_id):
    paths = {}
    with app.app_context():
        from app.shared.db import db
        from app.shared.models.core import CustomerAccount
        account = db.session.get(CustomerAccount, account_id)
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        paths["cache"] = f.name
        f.close()
        account.cache_db_path = paths["cache"]
        db.session.commit()
    return paths


def test_docs_index_empty(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get("/app/docs/")
        assert resp.status_code == 200
        assert b"No documents yet" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_docs_create(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "/docs/" in resp.headers["Location"]
        assert "/edit" in resp.headers["Location"]
    finally:
        os.unlink(paths["cache"])


def test_docs_create_spreadsheet(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "ods"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "/edit" in resp.headers["Location"]
    finally:
        os.unlink(paths["cache"])


def test_docs_create_presentation(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odp"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "/edit" in resp.headers["Location"]
    finally:
        os.unlink(paths["cache"])


def test_docs_create_invalid_type(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "exe"}, follow_redirects=False)
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


def test_docs_list_after_create(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        assert resp.status_code == 302

        resp = client.get("/app/docs/")
        assert resp.status_code == 200
        assert b"Untitled Document" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_docs_editor_page(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        location = resp.headers["Location"]
        doc_id = location.rsplit("/", 2)[-2]

        resp = client.get(f"/app/docs/{doc_id}/edit")
        assert resp.status_code == 200
        assert b"collabora" in resp.data.lower() or b"iframe" in resp.data.lower()
        assert b"Untitled Document" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_docs_rename(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        location = resp.headers["Location"]
        doc_id = location.rsplit("/", 2)[-2]

        resp = client.post(
            f"/app/docs/{doc_id}/rename",
            data={"name": "My Report"},
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["name"] == "My Report"
    finally:
        os.unlink(paths["cache"])


def test_docs_rename_empty(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        resp = client.post(f"/app/docs/{doc_id}/rename", data={"name": ""})
        assert resp.status_code == 400
    finally:
        os.unlink(paths["cache"])


def test_docs_rename_slash(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        resp = client.post(f"/app/docs/{doc_id}/rename", data={"name": "bad/name"})
        assert resp.status_code == 400
    finally:
        os.unlink(paths["cache"])


def test_docs_rename_too_long(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        resp = client.post(f"/app/docs/{doc_id}/rename", data={"name": "x" * 256})
        assert resp.status_code == 400
    finally:
        os.unlink(paths["cache"])


def test_docs_soft_delete(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        resp = client.post(f"/app/docs/{doc_id}/delete", follow_redirects=False)
        assert resp.status_code == 302

        resp = client.get("/app/docs/")
        assert resp.status_code == 200
        assert b"Trash" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_docs_restore(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        client.post(f"/app/docs/{doc_id}/delete")
        resp = client.post(f"/app/docs/{doc_id}/restore", follow_redirects=False)
        assert resp.status_code == 302

        resp = client.get("/app/docs/")
        assert resp.status_code == 200
        assert b"Untitled Document" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_docs_hard_delete_from_trash(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        client.post(f"/app/docs/{doc_id}/delete")
        resp = client.post(f"/app/docs/{doc_id}/delete", follow_redirects=False)
        assert resp.status_code == 302

        resp = client.get("/app/docs/")
        assert b"Untitled Document" not in resp.data
        assert b"Trash" not in resp.data
    finally:
        os.unlink(paths["cache"])


def test_docs_download(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        resp = client.get(f"/app/docs/{doc_id}/download")
        assert resp.status_code == 200
        assert resp.content_type == "application/vnd.oasis.opendocument.text"
        assert len(resp.data) > 0
    finally:
        os.unlink(paths["cache"])


def test_docs_download_deleted(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        client.post(f"/app/docs/{doc_id}/delete")
        resp = client.get(f"/app/docs/{doc_id}/download", follow_redirects=False)
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_odt(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        from app.modules.docs.services.templates import empty_odt
        buf = empty_odt()
        data = buf.read()

        resp = client.post(
            "/app/docs/upload",
            data={"file": (io.BytesIO(data), "test.odt")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/edit" in resp.headers["Location"]
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_invalid_extension(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post(
            "/app/docs/upload",
            data={"file": (io.BytesIO(b"bad"), "test.exe")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/edit" not in resp.headers["Location"]
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_ajax_odt(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        from app.modules.docs.services.templates import empty_odt
        buf = empty_odt()
        data = buf.read()

        resp = client.post(
            "/app/docs/upload",
            data={"file": (io.BytesIO(data), "test.odt")},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "doc_id" in body
        assert "editor_url" in body
        assert "/edit" in body["editor_url"]
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_ajax_no_account(authed_client, app):
    client, user_id, account_id = authed_client
    with client.session_transaction() as sess:
        sess["active_account_id"] = None
    resp = client.post(
        "/app/docs/upload",
        data={"file": (io.BytesIO(b"data"), "test.odt")},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert "error" in body


def test_docs_upload_ajax_no_file(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post(
            "/app/docs/upload",
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert "error" in body
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_ajax_invalid_extension(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post(
            "/app/docs/upload",
            data={"file": (io.BytesIO(b"bad"), "test.exe")},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert "Unsupported" in body["error"]
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_ajax_oversized(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        from unittest.mock import patch as upatch
        big = io.BytesIO(b"x" * (50 * 1024 * 1024 + 1))
        resp = client.post(
            "/app/docs/upload",
            data={"file": (big, "big.odt")},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert "50 MB" in body["error"]
    finally:
        os.unlink(paths["cache"])


def test_docs_empty_trash(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
        doc_id = resp.headers["Location"].rsplit("/", 2)[-2]

        client.post(f"/app/docs/{doc_id}/delete")
        resp = client.post("/app/docs/trash/empty", follow_redirects=False)
        assert resp.status_code == 302

        resp = client.get("/app/docs/")
        assert b"Trash" not in resp.data
    finally:
        os.unlink(paths["cache"])


def test_docs_no_account_redirect(authed_client, app):
    client, user_id, account_id = authed_client
    with client.session_transaction() as sess:
        sess["active_account_id"] = None
    resp = client.get("/app/docs/", follow_redirects=False)
    assert resp.status_code == 302


def test_docs_editor_nonexistent(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get("/app/docs/nonexistent/edit", follow_redirects=False)
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


def _fake_odt_bytes():
    from app.modules.docs.services.templates import empty_odt
    return empty_odt().read()


def _fake_ods_bytes():
    from app.modules.docs.services.templates import empty_ods
    return empty_ods().read()


def _fake_odp_bytes():
    from app.modules.docs.services.templates import empty_odp
    return empty_odp().read()


_CONVERSION_CASES = [
    ("docx", "odt"),
    ("xlsx", "ods"),
    ("pptx", "odp"),
]


@pytest.mark.parametrize("src_ext,target_ext", _CONVERSION_CASES)
def test_docs_upload_original_stored(authed_client, app, src_ext, target_ext):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        fake_content = b"PK\x03\x04 fake office document content"

        resp = client.post(
            "/app/docs/upload",
            data={"file": (io.BytesIO(fake_content), f"test.{src_ext}")},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "doc_id" in body
        assert "editor_url" in body

        from app.modules.docs.services import storage
        stored = storage.read_file(user_id, account_id, body["doc_id"])
        assert stored == fake_content

        sidecar = storage.read_sidecar(user_id, account_id, body["doc_id"])
        assert sidecar is not None
        assert sidecar["name"] == "test"
        assert sidecar["doc_type"] == target_ext
        assert sidecar["original_format"] == src_ext
    finally:
        os.unlink(paths["cache"])


@pytest.mark.parametrize("src_ext,target_ext", _CONVERSION_CASES)
def test_docs_upload_original_not_ajax(authed_client, app, src_ext, target_ext):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        fake_content = b"PK\x03\x04 fake office document content"

        resp = client.post(
            "/app/docs/upload",
            data={"file": (io.BytesIO(fake_content), f"test.{src_ext}")},
            content_type="multipart/form-data",
        )

        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_pdf_original_stored(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        fake_pdf = b"%PDF-1.4 fake pdf content"

        resp = client.post(
            "/app/docs/upload",
            data={"file": (io.BytesIO(fake_pdf), "contract.pdf")},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "doc_id" in body
        assert "editor_url" in body

        from app.modules.docs.services import storage
        stored = storage.read_file(user_id, account_id, body["doc_id"])
        assert stored == fake_pdf

        sidecar = storage.read_sidecar(user_id, account_id, body["doc_id"])
        assert sidecar is not None
        assert sidecar["name"] == "contract"
        assert sidecar["original_format"] == "pdf"
        assert sidecar["doc_type"] == "odg"
    finally:
        os.unlink(paths["cache"])


_PANDOC_FORMAT_CASES = [
    ("rtf", b"{\\rtf1 Hello}"),
    ("txt", b"Hello world"),
    ("md", b"# Hello\nWorld"),
    ("html", b"<html><body>Hello</body></html>"),
    ("epub", b"fake-epub-data"),
    ("csv", b"name,age\nAlice,30\nBob,25"),
    ("tsv", b"name\tage\nAlice\t30\nBob\t25"),
    ("ipynb", b'{"nbformat":4,"nbformat_minor":5,"metadata":{},"cells":[{"cell_type":"markdown","source":["# Hello"]}]}'),
]


@pytest.mark.parametrize("src_ext,content", _PANDOC_FORMAT_CASES)
def test_docs_upload_pandoc_format_success(authed_client, app, src_ext, content):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        fake_odt = _fake_odt_bytes()

        with patch(
            "app.shared.pandoc_formats.convert_to_odf",
            return_value=fake_odt,
        ) as mock_pandoc:
            resp = client.post(
                "/app/docs/upload",
                data={"file": (io.BytesIO(content), f"test.{src_ext}")},
                content_type="multipart/form-data",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        mock_pandoc.assert_called_once()
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert "doc_id" in body
        assert "editor_url" in body
    finally:
        os.unlink(paths["cache"])


def test_docs_upload_pandoc_format_failure(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch(
            "app.shared.pandoc_formats.convert_to_odf",
            return_value=None,
        ):
            resp = client.post(
                "/app/docs/upload",
                data={"file": (io.BytesIO(b"data"), "test.rtf")},
                content_type="multipart/form-data",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        assert resp.status_code == 500
        body = json.loads(resp.data)
        assert "error" in body
    finally:
        os.unlink(paths["cache"])


class TestConvertDocument:
    def test_convert_pdf_creates_new_editable_doc(self, authed_client, app):
        client, user_id, account_id = authed_client
        paths = _setup_test_env(app, account_id)
        try:
            from app.modules.docs.services import storage, cache_db
            from app.modules.docs.services.cache import get_cache_path
            from app.modules.docs.services.templates import empty_odg
            from app.shared.keys import get_user_key

            fake_pdf = b"%PDF-1.4 fake content"
            key = get_user_key(user_id)
            with app.app_context():
                from app.shared.db import db
                from app.shared.models.core import CustomerAccount
                account = db.session.get(CustomerAccount, account_id)
                conn = cache_db.open_cache(get_cache_path(account), key)
                try:
                    doc_id = "pdfdoc001"
                    # Seed as a *legacy* PDF stored with doc_type="odt" to prove
                    # the convert route corrects the target via target_odf_type.
                    cache_db.create_document(conn, doc_id, "Contract", "odt", account_id, file_size=0, original_format="pdf")
                    storage.write_file(user_id, account_id, doc_id, fake_pdf)
                    storage.write_sidecar(user_id, account_id, doc_id, {
                        "id": doc_id, "name": "Contract", "doc_type": "odt",
                        "original_format": "pdf", "account_id": account_id,
                    })
                    cache_db.update_file_size(conn, doc_id, len(fake_pdf))
                finally:
                    conn.close()

            converted_odg = empty_odg().read()
            with patch(
                "app.modules.docs.controllers.docs.collabora.convert_upload",
                return_value=io.BytesIO(converted_odg),
            ) as mock_convert:
                resp = client.post(
                    f"/app/docs/{doc_id}/convert",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )

            assert resp.status_code == 200
            body = json.loads(resp.data)
            assert "doc_id" in body
            assert "editor_url" in body
            new_doc_id = body["doc_id"]
            assert new_doc_id != doc_id

            # Regression guard: convert_upload MUST be asked for target "odg"
            # (the old code passed "odt" -> Collabora savefailed). Positional
            # call signature: convert_upload(stream, filename, target_type).
            mock_convert.assert_called_once()
            assert mock_convert.call_args.args[2] == "odg"

            with app.app_context():
                conn = cache_db.open_cache(get_cache_path(account), key)
                try:
                    original = cache_db.get_document(conn, doc_id)
                    assert original["original_format"] == "pdf"

                    new_doc = cache_db.get_document(conn, new_doc_id)
                    assert new_doc is not None
                    assert new_doc["name"] == "Contract"
                    assert new_doc["original_format"] is None
                    assert new_doc["doc_type"] == "odg"
                finally:
                    conn.close()

            assert storage.read_file(user_id, account_id, doc_id) == fake_pdf
            assert storage.read_file(user_id, account_id, new_doc_id) is not None
        finally:
            os.unlink(paths["cache"])

    def test_convert_already_editable_returns_error(self, authed_client, app):
        client, user_id, account_id = authed_client
        paths = _setup_test_env(app, account_id)
        try:
            from app.modules.docs.services import storage, cache_db
            from app.modules.docs.services.cache import get_cache_path
            from app.shared.keys import get_user_key
            from app.modules.docs.services.templates import empty_odt

            key = get_user_key(user_id)
            with app.app_context():
                from app.shared.db import db
                from app.shared.models.core import CustomerAccount
                account = db.session.get(CustomerAccount, account_id)
                conn = cache_db.open_cache(get_cache_path(account), key)
                try:
                    doc_id = "nativedoc01"
                    odt_data = empty_odt().read()
                    cache_db.create_document(conn, doc_id, "Native", "odt", account_id, file_size=0)
                    storage.write_file(user_id, account_id, doc_id, odt_data)
                    cache_db.update_file_size(conn, doc_id, len(odt_data))
                finally:
                    conn.close()

            resp = client.post(
                f"/app/docs/{doc_id}/convert",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 400
        finally:
            os.unlink(paths["cache"])

    def test_convert_not_found(self, authed_client, app):
        client, user_id, account_id = authed_client
        paths = _setup_test_env(app, account_id)
        try:
            resp = client.post(
                "/app/docs/nonexistent/convert",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 404
        finally:
            os.unlink(paths["cache"])
