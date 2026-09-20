import contextlib
import json
import os
from datetime import UTC, datetime

import pytest

from app.shared.db import db
from app.shared.keys import clear_user_key, set_user_key
from app.shared.models.core import CustomerAccount, DocShare, Domain, User


def _safe_unlink(path):
    with contextlib.suppress(OSError):
        os.unlink(path)


def _setup_test_env(app, account_id):
    paths = {}
    with app.app_context():
        from app.modules.docs.services.cache import get_cache_path

        account = db.session.get(CustomerAccount, account_id)
        paths["cache"] = get_cache_path(account)
        if os.path.exists(paths["cache"]):
            _safe_unlink(paths["cache"])
        db.session.commit()
    return paths


def _create_doc(client, doc_type="odt"):
    resp = client.post("/app/docs/new", data={"doc_type": doc_type}, follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["Location"]
    doc_id = location.rsplit("/", 2)[-2]
    return doc_id


def _make_share(**fields) -> DocShare:
    share = DocShare()
    for key, value in fields.items():
        setattr(share, key, value)
    return share


@pytest.fixture()
def share_client(app, client):
    user_id = None
    account_id = None
    with app.app_context():
        user = User()
        user.email = "owner@example.com"
        user.role = "customer"
        user.is_active = True
        user.password_hash = "x"
        db.session.add(user)
        db.session.flush()
        user_id = user.id

        domain = Domain()
        domain.name = "example.com"
        domain.is_active = True
        domain.status = "active"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.imap_tls = True
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()

        account = CustomerAccount()
        account.customer_id = user.id
        account.domain_id = domain.id
        account.email_address = "owner@example.com"
        account.auth_type = "password"
        account.username = "owner@example.com"
        account.cache_db_path = ""
        db.session.add(account)
        db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    with client.session_transaction() as sess:
        sess["role"] = "customer"
        sess["user_id"] = user_id
        sess["active_account_id"] = account_id

    yield client, user_id, account_id

    clear_user_key(user_id)


def test_list_shares_empty(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.get(f"/app/docs/{doc_id}/shares")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["shares"] == []
    finally:
        _safe_unlink(paths["cache"])


def test_add_share(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "external@gmail.com",
                    "permission": "view",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert len(data["shares"]) == 1
        assert data["shares"][0]["recipient_email"] == "external@gmail.com"
        assert data["shares"][0]["permission"] == "view"
        assert data["shares"][0]["share_type"] == "link"
    finally:
        _safe_unlink(paths["cache"])


def test_add_share_internal(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "colleague@example.com",
                    "permission": "write",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert len(data["shares"]) == 1
        assert data["shares"][0]["share_type"] == "internal"
        assert data["shares"][0]["permission"] == "write"
    finally:
        _safe_unlink(paths["cache"])


def test_add_share_multiple_emails(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "a@gmail.com, b@gmail.com",
                    "permission": "view",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert len(data["shares"]) == 2
    finally:
        _safe_unlink(paths["cache"])


def test_add_share_duplicate_skipped(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "a@gmail.com",
                    "permission": "view",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        resp = client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "a@gmail.com",
                    "permission": "view",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert len(data["shares"]) == 0
    finally:
        _safe_unlink(paths["cache"])


def test_add_share_invalid_permission(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "a@gmail.com",
                    "permission": "admin",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
    finally:
        _safe_unlink(paths["cache"])


def test_add_share_no_emails(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "",
                    "permission": "view",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
    finally:
        _safe_unlink(paths["cache"])


def test_add_share_nonexistent_doc(share_client, app):
    client, _user_id, account_id = share_client
    _setup_test_env(app, account_id)
    resp = client.post(
        "/app/docs/0000/shares",
        data=json.dumps(
            {
                "emails": "a@gmail.com",
                "permission": "view",
                "send_invite": False,
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 404


def test_revoke_share(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        resp = client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "a@gmail.com",
                    "permission": "view",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        share_id = data["shares"][0]["id"]

        resp = client.delete(f"/app/docs/{doc_id}/shares/{share_id}")
        assert resp.status_code == 200

        resp = client.get(f"/app/docs/{doc_id}/shares")
        data = json.loads(resp.data)
        assert len(data["shares"]) == 0
    finally:
        _safe_unlink(paths["cache"])


def test_revoke_share_not_owner(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        with app.app_context():
            share = _make_share(
                doc_id=doc_id,
                owner_user_id=9999,
                owner_account_id=9999,
                share_token="abc123",
                permission="view",
                share_type="link",
                recipient_email="x@gmail.com",
                doc_name="Test",
                doc_type="odt",
            )
            db.session.add(share)
            db.session.commit()
            share_id = share.id

        resp = client.delete(f"/app/docs/{doc_id}/shares/{share_id}")
        assert resp.status_code == 404
    finally:
        _safe_unlink(paths["cache"])


def test_public_share_view(share_client, app):
    client, user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        with app.app_context():
            share = _make_share(
                doc_id=doc_id,
                owner_user_id=user_id,
                owner_account_id=account_id,
                share_token="pubtoken123",
                permission="view",
                share_type="link",
                recipient_email="ext@gmail.com",
                doc_name="Test Doc",
                doc_type="odt",
            )
            db.session.add(share)
            db.session.commit()

        resp = client.get("/app/docs/s/pubtoken123")
        assert resp.status_code == 200
        assert b"Test Doc" in resp.data
        assert b"Shared by" in resp.data or b"Can view" in resp.data
        # Regression guard (HLD U13.20a): browser-level pinch-zoom must be
        # disabled on the share host page so touch gestures reach Collabora
        # instead of fighting the browser's page zoom on mobile.
        assert (
            b'name="viewport" content="width=device-width, initial-scale=1, '
            b'maximum-scale=1.0, user-scalable=no"' in resp.data
        )
        assert b"display: block; touch-action: none; }" in resp.data

        cookie_headers = [v for k, v in resp.headers if k == "Set-Cookie"]
        assert any("share_access=pubtoken123" in c for c in cookie_headers)
        share_cookie = next(c for c in cookie_headers if "share_access=pubtoken123" in c)
        assert "HttpOnly" in share_cookie
        assert "Secure" in share_cookie
        assert "SameSite=Lax" in share_cookie
        assert "Max-Age=28800" in share_cookie
    finally:
        _safe_unlink(paths["cache"])


def test_public_share_revoked(share_client, app):
    client, user_id, account_id = share_client
    _setup_test_env(app, account_id)
    with app.app_context():
        share = _make_share(
            doc_id="fake",
            owner_user_id=user_id,
            owner_account_id=account_id,
            share_token="revoked123",
            permission="view",
            share_type="link",
            recipient_email="x@gmail.com",
            doc_name="Revoked",
            doc_type="odt",
            revoked_at=datetime.now(UTC),
        )
        db.session.add(share)
        db.session.commit()

    resp = client.get("/app/docs/s/revoked123")
    assert resp.status_code == 404
    assert b"revoked" in resp.data.lower() or b"unavailable" in resp.data.lower()


def test_public_share_nonexistent(share_client, app):
    client, _user_id, account_id = share_client
    _setup_test_env(app, account_id)
    resp = client.get("/app/docs/s/doesnotexist")
    assert resp.status_code == 404


def test_public_share_records_access(share_client, app):
    client, user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        with app.app_context():
            share = _make_share(
                doc_id=doc_id,
                owner_user_id=user_id,
                owner_account_id=account_id,
                share_token="accesstok123",
                permission="view",
                share_type="link",
                recipient_email="x@gmail.com",
                doc_name="Access",
                doc_type="odt",
            )
            db.session.add(share)
            db.session.commit()
            share_id = share.id

        client.get("/app/docs/s/accesstok123")

        with app.app_context():
            share = db.session.get(DocShare, share_id)
            assert share is not None
            assert share.view_count == 1
            assert share.last_accessed_at is not None
    finally:
        _safe_unlink(paths["cache"])


def test_delete_doc_revokes_shares(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        client.post(
            f"/app/docs/{doc_id}/shares",
            data=json.dumps(
                {
                    "emails": "a@gmail.com",
                    "permission": "view",
                    "send_invite": False,
                }
            ),
            content_type="application/json",
        )

        client.post(f"/app/docs/{doc_id}/delete")

        with app.app_context():
            active = DocShare.query.filter_by(doc_id=doc_id, revoked_at=None).all()
            assert len(active) == 0
    finally:
        _safe_unlink(paths["cache"])


def test_rename_updates_shares(share_client, app):
    client, user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        with app.app_context():
            share = _make_share(
                doc_id=doc_id,
                owner_user_id=user_id,
                owner_account_id=account_id,
                share_token="renametok",
                permission="view",
                share_type="link",
                recipient_email="x@gmail.com",
                doc_name="Untitled Document",
                doc_type="odt",
            )
            db.session.add(share)
            db.session.commit()
            share_id = share.id

        client.post(f"/app/docs/{doc_id}/rename", data={"name": "New Name"})

        with app.app_context():
            share = db.session.get(DocShare, share_id)
            assert share is not None
            assert share.doc_name == "New Name"
    finally:
        _safe_unlink(paths["cache"])


def test_docs_index_shows_sidebar(share_client, app):
    client, _user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get("/app/docs/")
        assert resp.status_code == 200
        assert b"My Documents" in resp.data
        assert b"Shared with me" in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_docs_index_shared_section(share_client, app):
    client, user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        with app.app_context():
            share = _make_share(
                doc_id=doc_id,
                owner_user_id=user_id,
                owner_account_id=account_id,
                share_token="sharedview",
                permission="write",
                share_type="internal",
                recipient_email="owner@example.com",
                doc_name="Shared Doc",
                doc_type="odt",
            )
            db.session.add(share)
            db.session.commit()

        resp = client.get("/app/docs/?section=shared")
        assert resp.status_code == 200
        assert b"Shared Doc" in resp.data
        assert b"Can edit" in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_share_list_returns_stats(share_client, app):
    client, user_id, account_id = share_client
    paths = _setup_test_env(app, account_id)
    try:
        doc_id = _create_doc(client)
        with app.app_context():
            share = _make_share(
                doc_id=doc_id,
                owner_user_id=user_id,
                owner_account_id=account_id,
                share_token="stattok",
                permission="view",
                share_type="link",
                recipient_email="x@gmail.com",
                doc_name="Stats",
                doc_type="odt",
                view_count=5,
            )
            db.session.add(share)
            db.session.commit()

        resp = client.get(f"/app/docs/{doc_id}/shares")
        data = json.loads(resp.data)
        assert data["shares"][0]["view_count"] == 5
    finally:
        _safe_unlink(paths["cache"])
