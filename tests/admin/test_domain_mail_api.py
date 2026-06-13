import json
from unittest.mock import patch, MagicMock

from app.shared.db import db
from app.shared.models.core import User, Domain, CustomerAccount


def _create_domain_with_mail_api(app, name="synctest.com", mail_api_url="http://mail-api:8800", mail_api_key="test-key"):
    domain_id = None
    with app.app_context():
        domain = Domain(
            name=name,
            is_active=True,
            status="complete",
            imap_host=f"imap.{name}",
            imap_port=993,
            smtp_host=f"smtp.{name}",
            smtp_port=587,
            smtp_tls_mode="starttls",
            mail_api_url=mail_api_url,
            mail_api_key=mail_api_key,
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()
    return domain_id


def _create_local_account(app, domain_id, email):
    with app.app_context():
        user = User(email=email, role="customer", is_active=True)
        db.session.add(user)
        db.session.flush()
        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain_id,
            email_address=email,
            auth_type="password",
            username=email,
        )
        db.session.add(account)
        db.session.commit()


@patch("app.admin.controllers.admin.log_audit")
def test_test_mail_api_connection_success(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("app.admin.services.mail_server.http_client.requests.request", return_value=mock_resp):
        resp = client.post(f"/admin/domains/{domain_id}/test-mail-api")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


@patch("app.admin.controllers.admin.log_audit")
def test_test_mail_api_connection_failure(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)

    import requests as real_requests
    with patch("app.admin.services.mail_server.http_client.requests.request", side_effect=real_requests.ConnectionError("refused")):
        resp = client.post(f"/admin/domains/{domain_id}/test-mail-api")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False


@patch("app.admin.controllers.admin.log_audit")
def test_test_mail_api_connection_no_url(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app, mail_api_url="")

    resp = client.post(f"/admin/domains/{domain_id}/test-mail-api")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False


@patch("app.admin.controllers.admin.log_audit")
def test_test_mail_api_uses_form_values(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app, mail_api_url="", mail_api_key="")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("app.admin.services.mail_server.http_client.requests.request", return_value=mock_resp):
        resp = client.post(
            f"/admin/domains/{domain_id}/test-mail-api",
            data={"mail_api_url": "http://testhost:8800", "mail_api_key": "new-key"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


@patch("app.admin.controllers.admin.log_audit")
def test_sync_preview_diff(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)
    _create_local_account(app, domain_id, "local@synctest.com")

    mock_mail = MagicMock()
    mock_mail.list_users.return_value = [
        {"email": "remote@synctest.com"},
        {"email": "local@synctest.com"},
    ]
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_mail):
        resp = client.post(f"/admin/domains/{domain_id}/sync-preview")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["remote_only"] == ["remote@synctest.com"]
    assert data["local_only"] == []
    assert "local@synctest.com" in data["in_sync"]


@patch("app.admin.controllers.admin.log_audit")
def test_sync_preview_local_only(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)
    _create_local_account(app, domain_id, "orphan@synctest.com")

    mock_mail = MagicMock()
    mock_mail.list_users.return_value = []
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_mail):
        resp = client.post(f"/admin/domains/{domain_id}/sync-preview")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["remote_only"] == []
    assert data["local_only"] == ["orphan@synctest.com"]
    assert data["in_sync"] == []


@patch("app.admin.controllers.admin.log_audit")
def test_sync_preview_no_mail_api(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app, mail_api_url="")

    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=None):
        resp = client.post(f"/admin/domains/{domain_id}/sync-preview")

    assert resp.status_code == 400


@patch("app.admin.controllers.admin.log_audit")
def test_sync_preview_all_in_sync(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)
    _create_local_account(app, domain_id, "synced@synctest.com")

    mock_mail = MagicMock()
    mock_mail.list_users.return_value = [{"email": "synced@synctest.com"}]
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_mail):
        resp = client.post(f"/admin/domains/{domain_id}/sync-preview")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["remote_only"] == []
    assert data["local_only"] == []
    assert data["in_sync"] == ["synced@synctest.com"]


@patch("app.admin.controllers.admin.log_audit")
def test_sync_apply_create_locally(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)

    resp = client.post(
        f"/admin/domains/{domain_id}/sync-apply",
        data=json.dumps({"create_locally": ["new@synctest.com"], "create_remotely": [], "soft_delete_locally": []}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "new@synctest.com" in data["created_locally"]

    with app.app_context():
        user = User.query.filter_by(email="new@synctest.com").first()
        assert user is not None
        assert user.role == "customer"
        acc = CustomerAccount.query.filter_by(email_address="new@synctest.com").first()
        assert acc is not None


@patch("app.admin.controllers.admin.log_audit")
def test_sync_apply_create_remotely(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)
    _create_local_account(app, domain_id, "localonly@synctest.com")

    mock_mail = MagicMock()
    mock_mail.add_user.return_value = {"status": "ok"}
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_mail):
        resp = client.post(
            f"/admin/domains/{domain_id}/sync-apply",
            data=json.dumps({"create_locally": [], "create_remotely": ["localonly@synctest.com"], "soft_delete_locally": []}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "localonly@synctest.com" in data["created_remotely"]
    mock_mail.add_user.assert_called_once()

    with app.app_context():
        acc = CustomerAccount.query.filter_by(email_address="localonly@synctest.com").first()
        assert acc.is_active is True


@patch("app.admin.controllers.admin.log_audit")
def test_sync_apply_soft_delete_locally(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)
    _create_local_account(app, domain_id, "todelete@synctest.com")

    resp = client.post(
        f"/admin/domains/{domain_id}/sync-apply",
        data=json.dumps({"create_locally": [], "create_remotely": [], "soft_delete_locally": ["todelete@synctest.com"]}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "todelete@synctest.com" in data["soft_deleted_locally"]

    with app.app_context():
        acc = CustomerAccount.query.filter_by(email_address="todelete@synctest.com").first()
        assert acc.is_active is False


@patch("app.admin.controllers.admin.log_audit")
def test_sync_apply_combined(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain_with_mail_api(app)
    _create_local_account(app, domain_id, "existing@synctest.com")

    mock_mail = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_mail):
        resp = client.post(
            f"/admin/domains/{domain_id}/sync-apply",
            data=json.dumps({
                "create_locally": ["remote@synctest.com"],
                "create_remotely": ["existing@synctest.com"],
                "soft_delete_locally": [],
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "remote@synctest.com" in data["created_locally"]
    assert "existing@synctest.com" in data["created_remotely"]


@patch("app.admin.controllers.admin.log_audit")
def test_domain_review_saves_mail_api_fields(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="mailfields.com",
            is_active=True,
            status="complete",
            imap_host="imap.mailfields.com",
            imap_port=993,
            smtp_host="smtp.mailfields.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_discovery = {
        "imap_primary": MagicMock(host="imap.mailfields.com", port=993),
        "smtp_primary": MagicMock(host="smtp.mailfields.com", port=587, tls_mode="starttls"),
        "imap_candidates": [MagicMock()],
        "smtp_candidates": [MagicMock()],
    }
    with patch("app.admin.controllers.admin.discover_domain_settings", return_value=mock_discovery):
        resp = client.post(
            f"/admin/domains/{domain_id}/review",
            data={
                "imap_host": "imap.mailfields.com",
                "imap_port": "993",
                "smtp_host": "smtp.mailfields.com",
                "smtp_port": "587",
                "smtp_tls_mode": "starttls",
                "mail_api_url": "http://new-api:8800",
                "mail_api_key": "new-key-123",
            },
        )

    assert resp.status_code == 302
    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain.mail_api_url == "http://new-api:8800"
        assert domain.mail_api_key == "new-key-123"


@patch("app.admin.controllers.admin.log_audit")
def test_per_domain_mail_client_used_for_domain_sync(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="perdomain.com",
            is_active=True,
            status="complete",
            imap_host="imap.perdomain.com",
            imap_port=993,
            smtp_host="smtp.perdomain.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            mail_api_url="http://custom-api:8800",
            mail_api_key="custom-key",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_mail = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_mail) as mock_factory:
        resp = client.post(f"/admin/domains/{domain_id}/toggle")

    assert resp.status_code == 302
    mock_factory.assert_called()
    mock_mail.remove_domain.assert_called_once_with("perdomain.com")


@patch("app.admin.controllers.admin.log_audit")
def test_per_domain_mail_client_fallback_to_global(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="fallback.com",
            is_active=True,
            status="complete",
            imap_host="imap.fallback.com",
            imap_port=993,
            smtp_host="smtp.fallback.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    global_client = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=global_client) as mock_factory:
        resp = client.post(f"/admin/domains/{domain_id}/toggle")

    assert resp.status_code == 302
    mock_factory.assert_called()
    global_client.remove_domain.assert_called_once_with("fallback.com")
