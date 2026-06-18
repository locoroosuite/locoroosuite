import io
import json
import os
import tempfile


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


def _upload(client, raw, filename):
    resp = client.post(
        "/app/docs/upload",
        data={"file": (io.BytesIO(raw), filename)},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["doc_id"]


def test_api_list_returns_documents(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _upload(client, b"%PDF-1.4 fake", "contract.pdf")
        resp = client.get("/app/docs/api/list?account_id=%s" % account_id)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["account_id"] == account_id
        docs = body["documents"]
        assert len(docs) == 1
        assert docs[0]["id"] == doc_id
        assert docs[0]["name"] == "contract"
        assert docs[0]["ext"] == "pdf"
        assert docs[0]["original_format"] == "pdf"
        for key in ("id", "name", "doc_type", "original_format", "file_size", "updated_at"):
            assert key in docs[0]
    finally:
        os.unlink(paths["cache"])


def test_api_list_search_filter(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        _upload(client, b"%PDF-1.4 fake", "contract.pdf")
        _upload(client, b"PK fake xlsx", "budget.xlsx")

        resp = client.get("/app/docs/api/list?account_id=%s&q=contract" % account_id)
        docs = resp.get_json()["documents"]
        assert len(docs) == 1
        assert docs[0]["name"] == "contract"

        resp = client.get("/app/docs/api/list?account_id=%s&q=nomatch" % account_id)
        assert resp.get_json()["documents"] == []
    finally:
        os.unlink(paths["cache"])


def test_download_with_account_id_param(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        raw = b"%PDF-1.4 specific content"
        doc_id = _upload(client, raw, "contract.pdf")
        resp = client.get("/app/docs/%s/download?account_id=%s" % (doc_id, account_id))
        assert resp.status_code == 200
        assert resp.data == raw
        cd = resp.headers.get("Content-Disposition", "")
        assert "contract.pdf" in cd
    finally:
        os.unlink(paths["cache"])


def test_download_falls_back_to_session_account(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        raw = b"%PDF-1.4 fallback"
        doc_id = _upload(client, raw, "note.pdf")
        # No account_id query param: must use the session active_account_id.
        resp = client.get("/app/docs/%s/download" % doc_id)
        assert resp.status_code == 200
        assert resp.data == raw
    finally:
        os.unlink(paths["cache"])


def test_download_wrong_account_owner_404(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _upload(client, b"%PDF-1.4", "contract.pdf")
        resp = client.get("/app/docs/%s/download?account_id=999999" % doc_id)
        assert resp.status_code == 404
    finally:
        os.unlink(paths["cache"])


def test_api_list_uses_session_when_no_param(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        _upload(client, b"%PDF-1.4 fake", "contract.pdf")
        resp = client.get("/app/docs/api/list")
        assert resp.status_code == 200
        assert resp.get_json()["account_id"] == account_id
    finally:
        os.unlink(paths["cache"])
