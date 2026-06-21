from unittest.mock import patch, MagicMock


from app.shared.db import db
from app.shared.models.core import User, Domain, CustomerAccount


@patch("app.admin.controllers.admin.log_audit")
@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={
        "imap_primary": MagicMock(host="imap.test.com", port=993),
        "smtp_primary": MagicMock(host="smtp.test.com", port=587, tls_mode="starttls"),
        "imap_candidates": [MagicMock()],
        "smtp_candidates": [MagicMock()],
    },
)
def test_admin_create_domain_calls_mail_api(mock_discover, mock_audit, admin_client):
    client, _ = admin_client
    mock_mail = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client", return_value=mock_mail):
        resp = client.post("/admin/domains/new", data={"name": "mailapi.com"})
    assert resp.status_code == 302
    mock_mail.add_domain.assert_called_once_with("mailapi.com")


@patch("app.admin.controllers.admin.log_audit")
@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={"imap_primary": None, "smtp_primary": None, "imap_candidates": [], "smtp_candidates": []},
)
def test_admin_create_domain_mail_api_failure_graceful(mock_discover, mock_audit, admin_client, app):
    client, _ = admin_client
    mock_mail = MagicMock()
    mock_mail.add_domain.side_effect = Exception("connection refused")
    with patch("app.admin.services.mail_server.get_mail_client", return_value=mock_mail):
        resp = client.post("/admin/domains/new", data={"name": "fail.com"})
    assert resp.status_code == 302
    with app.app_context():
        domain = Domain.query.filter_by(name="fail.com").first()
        assert domain is not None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_domain_calls_mail_api(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="toggle-mailapi.com",
            is_active=True,
            status="complete",
            imap_host="imap.test.com",
            imap_port=993,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_mail = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client", return_value=mock_mail):
        resp = client.post(f"/admin/domains/{domain_id}/toggle")
    assert resp.status_code == 302
    mock_mail.remove_domain.assert_called_once_with("toggle-mailapi.com")


@patch("app.admin.controllers.admin.log_audit")
def test_admin_update_domain_calls_mail_api(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="update-mailapi.com",
            is_active=True,
            status="complete",
            imap_host="old.imap.com",
            imap_port=993,
            smtp_host="old.smtp.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_mail = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client", return_value=mock_mail):
        resp = client.post(
            f"/admin/domains/{domain_id}/update",
            data={
                "imap_host": "new.imap.com",
                "imap_port": "993",
                "smtp_host": "new.smtp.com",
                "smtp_port": "587",
                "smtp_tls_mode": "starttls",
            },
        )
    assert resp.status_code == 302
    mock_mail.add_domain.assert_called_once_with("update-mailapi.com")


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_with_mailbox(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="mailbox-test.com",
            is_active=True,
            status="complete",
            imap_host="imap.test.com",
            imap_port=993,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_mail = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client", return_value=mock_mail):
        resp = client.post(
            "/admin/customers/new",
            data={
                "username": "user",
                "domain_id": str(domain_id),
                "password": "initial-pass",
                "create_mode": "password",
            },
        )
    assert resp.status_code == 302
    mock_mail.add_user.assert_called_once_with("user@mailbox-test.com", "initial-pass")

    with app.app_context():
        account = CustomerAccount.query.filter_by(email_address="user@mailbox-test.com").first()
        assert account is not None
        assert account.auth_type == "password"


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_without_mailbox(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="plain-test.com",
            is_active=True,
            status="complete",
            imap_host="imap.test.com",
            imap_port=993,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_mail = MagicMock()
    mock_mail.check_user.return_value = False
    with patch("app.admin.services.mail_server.get_mail_client", return_value=mock_mail):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "plain", "domain_id": str(domain_id), "create_mode": "invite"},
        )
    assert resp.status_code == 200
    mock_mail.add_user.assert_not_called()

    with app.app_context():
        user = User.query.filter_by(email="plain@plain-test.com").first()
        assert user is not None
        assert user.role == "customer"


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_mail_api_failure_rollback(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="mailfail.com",
            is_active=True,
            status="complete",
            imap_host="imap.test.com",
            imap_port=993,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_mail = MagicMock()
    mock_mail.add_user.side_effect = Exception("connection refused")
    with patch("app.admin.services.mail_server.get_mail_client", return_value=mock_mail):
        resp = client.post(
            "/admin/customers/new",
            data={
                "username": "user",
                "domain_id": str(domain_id),
                "password": "pass",
                "create_mode": "password",
            },
            follow_redirects=True,
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Failed to create mailbox" in html
    with app.app_context():
        user = User.query.filter_by(email="user@mailfail.com").first()
        assert user is None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_no_mail_api_configured(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="nomapi.com",
            is_active=True,
            status="complete",
            imap_host="imap.test.com",
            imap_port=993,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    with patch("app.admin.services.mail_server.get_mail_client", return_value=None):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "nomapi", "domain_id": str(domain_id), "password": "pass", "create_mode": "password"},
        )
    assert resp.status_code == 302
