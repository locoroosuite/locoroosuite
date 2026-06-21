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


def _create_doc(client):
    resp = client.post("/app/docs/new", data={"doc_type": "odt"}, follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["Location"]
    doc_id = location.rsplit("/", 2)[-2]
    return doc_id


def test_wopi_check_file_info_no_token(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(f"/app/docs/wopi/files/{doc_id}")
        assert resp.status_code == 401
    finally:
        os.unlink(paths["cache"])


def test_wopi_check_file_info_invalid_token(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(f"/app/docs/wopi/files/{doc_id}?access_token=invalid")
        assert resp.status_code == 401
    finally:
        os.unlink(paths["cache"])


def test_wopi_check_file_info_valid_token(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)

        with app.app_context():
            from app.modules.docs.services.wopi_token import generate_token
            token = generate_token(doc_id, user_id, account_id, writable=True)

        resp = client.post(f"/app/docs/wopi/files/{doc_id}?access_token={token}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["BaseFileName"].endswith(".odt")
        assert data["UserId"] == str(user_id)
        assert data["UserCanWrite"] is True
        assert data["ReadOnly"] is False
    finally:
        os.unlink(paths["cache"])


def test_wopi_check_file_info_wrong_doc_id(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)

        with app.app_context():
            from app.modules.docs.services.wopi_token import generate_token
            token = generate_token(doc_id, user_id, account_id, writable=True)

        resp = client.post(f"/app/docs/wopi/files/wrong-id?access_token={token}")
        assert resp.status_code == 403
    finally:
        os.unlink(paths["cache"])


def test_wopi_get_file(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)

        with app.app_context():
            from app.modules.docs.services.wopi_token import generate_token
            token = generate_token(doc_id, user_id, account_id, writable=True)

        resp = client.get(f"/app/docs/wopi/files/{doc_id}/contents?access_token={token}")
        assert resp.status_code == 200
        assert len(resp.data) > 0
    finally:
        os.unlink(paths["cache"])


def test_wopi_get_file_no_token(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.get(f"/app/docs/wopi/files/{doc_id}/contents")
        assert resp.status_code == 401
    finally:
        os.unlink(paths["cache"])


def test_wopi_put_file(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)

        with app.app_context():
            from app.modules.docs.services.wopi_token import generate_token
            token = generate_token(doc_id, user_id, account_id, writable=True)

        new_content = b"updated document content for testing"
        resp = client.post(
            f"/app/docs/wopi/files/{doc_id}/contents?access_token={token}",
            data=new_content,
            content_type="application/octet-stream",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"

        resp = client.get(f"/app/docs/wopi/files/{doc_id}/contents?access_token={token}")
        assert resp.status_code == 200
        assert resp.data == new_content
    finally:
        os.unlink(paths["cache"])


def test_wopi_put_file_readonly(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)

        with app.app_context():
            from app.modules.docs.services.wopi_token import generate_token
            token = generate_token(doc_id, user_id, account_id, writable=False)

        resp = client.post(
            f"/app/docs/wopi/files/{doc_id}/contents?access_token={token}",
            data=b"should fail",
            content_type="application/octet-stream",
        )
        assert resp.status_code == 403
    finally:
        os.unlink(paths["cache"])


def test_wopi_put_file_no_token(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(
            f"/app/docs/wopi/files/{doc_id}/contents",
            data=b"test",
            content_type="application/octet-stream",
        )
        assert resp.status_code == 401
    finally:
        os.unlink(paths["cache"])
