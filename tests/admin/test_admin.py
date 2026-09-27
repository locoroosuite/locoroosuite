from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import CustomerAccount, Domain, DomainDnsConfig, User


def test_admin_login_page(client, app, _clean_db):
    with app.app_context():
        user = User()
        user.email = "admin@example.com"
        user.role = "admin"
        user.is_active = True
        user.password_hash = generate_password_hash("admin123")
        db.session.add(user)
        db.session.commit()
    resp = client.get("/admin/login")
    assert resp.status_code == 200


@patch("app.admin.controllers.auth.log_audit")
@patch("app.admin.controllers.auth.clear_failed_login")
@patch("app.admin.controllers.auth.is_locked", return_value=False)
def test_admin_login_post_success(mock_locked, mock_clear, mock_audit, app, client, _clean_db):
    with app.app_context():
        user = User()
        user.email = "admin@example.com"
        user.role = "admin"
        user.is_active = True
        user.password_hash = generate_password_hash("admin123")
        db.session.add(user)
        db.session.commit()

    resp = client.post("/admin/login", data={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 302
    assert "/admin/" in resp.headers["Location"]


def test_admin_dashboard(admin_client):
    client, _ = admin_client
    resp = client.get("/admin/")
    assert resp.status_code == 200


def test_admin_domains_page(admin_client):
    client, _ = admin_client
    resp = client.get("/admin/domains")
    assert resp.status_code == 200


@patch("app.admin.services.health_checks._tcp_check", return_value=False)
@patch("app.admin.services.health_checks._collabora_check", return_value=False)
def test_admin_domains_page_with_domains(mock_collabora, mock_tcp, admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()

        dns_cfg = DomainDnsConfig()
        dns_cfg.domain_id = domain.id
        dns_cfg.is_self_hosted = True
        dns_cfg.dkim_selector = "default"
        dns_cfg.dmarc_policy = "none"
        db.session.add(dns_cfg)
        db.session.commit()

    resp = client.get("/admin/domains")
    assert resp.status_code == 200
    assert b"example.com" in resp.data
    assert b"Self-hosted" in resp.data
    assert b"Checking services" in resp.data


@patch("app.admin.services.health_checks._tcp_check", return_value=True)
@patch("app.admin.services.health_checks._collabora_check", return_value=True)
def test_admin_domains_page_with_connected_services(mock_collabora, mock_tcp, admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "connected.example.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.carddav_host = "carddav.example.com"
        domain.carddav_port = 5232
        domain.caldav_host = "caldav.example.com"
        domain.caldav_port = 5232
        db.session.add(domain)
        db.session.commit()

    resp = client.get("/admin/domains")
    assert resp.status_code == 200
    assert b"connected.example.com" in resp.data


def test_admin_domains_page_with_inactive_domain(admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "inactive.example.com"
        domain.is_active = False
        domain.status = "draft"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.commit()

    resp = client.get("/admin/domains")
    assert resp.status_code == 200
    assert b"inactive.example.com" in resp.data
    assert b"Disabled" in resp.data


def test_admin_managers_page(admin_client):
    client, _ = admin_client
    resp = client.get("/admin/managers")
    assert resp.status_code == 200


def test_admin_customers_page(admin_client):
    client, _ = admin_client
    resp = client.get("/admin/customers")
    assert resp.status_code == 200


def test_admin_imports_page(admin_client):
    client, _ = admin_client
    resp = client.get("/admin/imports")
    assert resp.status_code == 200


def test_admin_assignments_page(admin_client):
    client, _ = admin_client
    resp = client.get("/admin/assignments")
    assert resp.status_code == 200


@patch("app.admin.controllers.admin.log_audit")
@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={
        "imap_primary": None,
        "smtp_primary": None,
        "imap_candidates": [],
        "smtp_candidates": [],
    },
)
def test_admin_create_domain(mock_discover, mock_audit, admin_client):
    client, _ = admin_client
    resp = client.post("/admin/domains/new", data={"name": "test.com"})
    assert resp.status_code == 302


@patch("app.admin.controllers.admin.log_audit")
@patch("app.admin.controllers.admin.generate_password_hash", return_value="hashed")
def test_admin_create_manager(mock_hash, mock_audit, admin_client):
    client, _ = admin_client
    resp = client.post(
        "/admin/managers/new", data={"email": "mgr@example.com", "password": "secret"}
    )
    assert resp.status_code == 302


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()
    resp = client.post(
        "/admin/customers/new",
        data={"username": "cust", "domain_id": domain_id, "create_mode": "invite"},
    )
    assert resp.status_code == 200


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_password_includes_login_link(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    with patch("app.admin.services.mail_server.get_mail_client", return_value=MagicMock()):
        resp = client.post(
            "/admin/customers/new",
            data={
                "username": "newuser",
                "domain_id": str(domain_id),
                "password": "secret123",
                "create_mode": "password",
            },
            follow_redirects=True,
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "newuser@example.com created with password" in html
    assert "/app/login" in html
    assert "click here" in html


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_invite_rejects_existing_mailbox(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_client = MagicMock()
    mock_client.check_user.return_value = True
    with patch(
        "app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_client
    ):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "existing", "domain_id": str(domain_id), "create_mode": "invite"},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "already exists on the mail server" in html
    assert "sync email accounts" in html

    with app.app_context():
        assert User.query.filter_by(email="existing@example.com").first() is None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_invite_no_mail_api_proceeds(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=None):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "newuser", "domain_id": str(domain_id), "create_mode": "invite"},
        )
    assert resp.status_code == 200


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_existing_user_with_sync_link(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        existing = User()
        existing.email = "dup@example.com"
        existing.role = "customer"
        db.session.add(existing)
        db.session.commit()

    mock_client = MagicMock()
    with patch(
        "app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_client
    ):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "dup", "domain_id": str(domain_id), "create_mode": "invite"},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "already exists" in html
    assert "sync email accounts" in html


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_existing_user_no_mail_api(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        existing = User()
        existing.email = "dup@example.com"
        existing.role = "customer"
        db.session.add(existing)
        db.session.commit()

    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=None):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "dup", "domain_id": str(domain_id), "create_mode": "invite"},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "already exists" in html
    assert "sync email accounts" not in html


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_password_rollback_on_mail_api_failure(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_client = MagicMock()
    mock_client.add_user.side_effect = Exception("User already exists")
    with patch(
        "app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_client
    ):
        resp = client.post(
            "/admin/customers/new",
            data={
                "username": "failuser",
                "domain_id": str(domain_id),
                "password": "secret",
                "create_mode": "password",
            },
            follow_redirects=True,
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Failed to create mailbox" in html
    assert "sync email accounts" in html

    with app.app_context():
        assert User.query.filter_by(email="failuser@example.com").first() is None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_domain(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "toggle.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.toggle.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.toggle.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(f"/admin/domains/{domain_id}/toggle")
    assert resp.status_code == 302


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_customer(mock_audit, admin_client, app):
    client, _ = admin_client
    cust_id = None
    with app.app_context():
        cust = User()
        cust.email = "toggle-cust@example.com"
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id
        db.session.commit()

    resp = client.post(f"/admin/customers/{cust_id}/toggle")
    assert resp.status_code == 302


@patch("app.admin.controllers.admin.log_audit")
def test_admin_update_domain(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "update.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "old.imap.com"
        domain.imap_port = 993
        domain.smtp_host = "old.smtp.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

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


def test_admin_logout(admin_client):
    client, _ = admin_client
    resp = client.get("/logout")
    assert resp.status_code == 302


@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={
        "imap_primary": None,
        "smtp_primary": None,
        "imap_candidates": [],
        "smtp_candidates": [],
    },
)
def test_admin_review_domain_page(mock_discover, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "review.com"
        domain.is_active = True
        domain.status = "review"
        domain.imap_host = ""
        domain.imap_port = 993
        domain.smtp_host = ""
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.get(f"/admin/domains/{domain_id}/review")
    assert resp.status_code == 302
    assert f"/admin/domains/{domain_id}/review/mail" in resp.headers["Location"]

    resp = client.get(f"/admin/domains/{domain_id}/review/mail")
    assert resp.status_code == 200


def _create_review_domain(app, name):
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = name
        domain.is_active = True
        domain.status = "review"
        domain.imap_host = ""
        domain.imap_port = 993
        domain.smtp_host = ""
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()
    return domain_id


def test_admin_review_dav_page_toast_feedback(admin_client, app):
    client, _ = admin_client
    domain_id = _create_review_domain(app, "toast-dav.com")

    resp = client.get(f"/admin/domains/{domain_id}/review/dav")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "dav-save-result" not in html
    assert "window.LR.notifySuccess('Contacts, calendar & chat settings saved.')" in html


@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={
        "imap_primary": None,
        "smtp_primary": None,
        "imap_candidates": [],
        "smtp_candidates": [],
    },
)
def test_admin_review_mail_page_toast_feedback(mock_discover, admin_client, app):
    client, _ = admin_client
    domain_id = _create_review_domain(app, "toast-mail.com")

    resp = client.get(f"/admin/domains/{domain_id}/review/mail")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "mail-save-result" not in html
    assert "window.LR.notifySuccess('Mail settings saved.')" in html


def test_admin_review_selfhosted_page_toast_feedback(admin_client, app):
    client, _ = admin_client
    domain_id = _create_review_domain(app, "toast-selfhosted.com")

    resp = client.get(f"/admin/domains/{domain_id}/review/self-hosted")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for removed_id in (
        "self-hosted-save-result",
        "self-hosted-error",
        "conn-result",
        "dkim-generate-result",
        "dkim-regenerate-result",
    ):
        assert removed_id not in html
    assert "window.LR.notifySuccess('Settings saved.')" in html
    assert "window.LR.notifySuccess('Self-hosted enabled.')" in html


@patch("app.admin.controllers.admin.log_audit")
def test_admin_update_domain_carddav(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "carddav-update.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(
        f"/admin/domains/{domain_id}/update",
        data={
            "imap_host": "imap.test.com",
            "imap_port": "993",
            "smtp_host": "smtp.test.com",
            "smtp_port": "587",
            "smtp_tls_mode": "starttls",
            "carddav_host": "dav.test.com",
            "carddav_port": "5232",
            "carddav_use_tls": "1",
        },
    )
    assert resp.status_code == 302

    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        assert domain.carddav_host == "dav.test.com"
        assert domain.carddav_port == 5232
        assert domain.carddav_use_tls is True


@patch("app.admin.controllers.admin.log_audit")
def test_admin_update_domain_carddav_clear(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "carddav-clear.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.carddav_host = "dav.test.com"
        domain.carddav_port = 5232
        domain.carddav_use_tls = True
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(
        f"/admin/domains/{domain_id}/update",
        data={
            "imap_host": "imap.test.com",
            "imap_port": "993",
            "smtp_host": "smtp.test.com",
            "smtp_port": "587",
            "smtp_tls_mode": "starttls",
            "carddav_host": "",
            "carddav_port": "",
        },
    )
    assert resp.status_code == 302

    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        assert domain.carddav_host is None
        assert domain.carddav_use_tls is False


@patch("app.admin.controllers.admin.log_audit")
@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={
        "imap_primary": None,
        "smtp_primary": None,
        "imap_candidates": [],
        "smtp_candidates": [],
    },
)
def test_admin_review_domain_saves_carddav(mock_discover, mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "carddav-review.com"
        domain.is_active = True
        domain.status = "review"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(
        f"/admin/domains/{domain_id}/review",
        data={
            "imap_host": "imap.test.com",
            "imap_port": "993",
            "smtp_host": "smtp.test.com",
            "smtp_port": "587",
            "smtp_tls_mode": "starttls",
            "carddav_host": "carddav.test.com",
            "carddav_port": "8443",
            "carddav_use_tls": "1",
        },
    )
    assert resp.status_code == 302

    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        assert domain.carddav_host == "carddav.test.com"
        assert domain.carddav_port == 8443
        assert domain.carddav_use_tls is True


@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={
        "imap_primary": None,
        "smtp_primary": None,
        "imap_candidates": [],
        "smtp_candidates": [],
    },
)
def test_admin_review_domain_page_shows_carddav_fields(mock_discover, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "carddav-ui.com"
        domain.is_active = True
        domain.status = "review"
        domain.imap_host = ""
        domain.imap_port = 993
        domain.smtp_host = ""
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.carddav_host = "existing-dav.com"
        domain.carddav_port = 5232
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.get(f"/admin/domains/{domain_id}/review/dav")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "CardDAV" in html
    assert 'name="carddav_host"' in html
    assert 'name="carddav_port"' in html
    assert 'name="carddav_use_tls"' in html
    assert "existing-dav.com" in html


@patch("app.admin.services.health_checks._tcp_check", return_value=True)
@patch("app.admin.services.health_checks._collabora_check", return_value=True)
@patch("app.admin.services.health_checks._check_mail_api", return_value="not_configured")
def test_admin_domains_health_json(mock_mail_api, mock_collabora, mock_tcp, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "health.example.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.get("/admin/api/domains/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert "imap" in data[str(domain_id)]
    assert "smtp" in data[str(domain_id)]


@patch("app.admin.controllers.admin.log_audit")
@patch("app.admin.controllers.admin._sync_domain_to_mail_api")
def test_admin_save_mail_config(mock_sync, mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "mail-save.com"
        domain.is_active = True
        domain.status = "review"
        domain.imap_host = "old-imap.com"
        domain.imap_port = 993
        domain.smtp_host = "old-smtp.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(
        f"/admin/domains/{domain_id}/mail-config",
        data={
            "imap_host": "new-imap.com",
            "imap_port": "993",
            "smtp_host": "new-smtp.com",
            "smtp_port": "465",
            "smtp_tls_mode": "tls",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        assert domain.imap_host == "new-imap.com"
        assert domain.smtp_port == 465
        assert domain.smtp_tls_mode == "tls"


@patch("app.admin.controllers.admin.log_audit")
def test_admin_save_dav_config(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "dav-save.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(
        f"/admin/domains/{domain_id}/dav-config",
        data={
            "caldav_host": "caldav.new.com",
            "caldav_port": "8443",
            "caldav_use_tls": "1",
            "carddav_host": "carddav.new.com",
            "carddav_port": "8444",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        assert domain.caldav_host == "caldav.new.com"
        assert domain.caldav_port == 8443
        assert domain.caldav_use_tls is True
        assert domain.carddav_host == "carddav.new.com"
        assert domain.carddav_port == 8444


def test_admin_domain_accounts_json(admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "accounts-test.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        cust = User()
        cust.email = "user@accounts-test.com"
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount()
        account.customer_id = cust.id
        account.domain_id = domain_id
        account.email_address = "user@accounts-test.com"
        account.auth_type = "password"
        account.username = "user@accounts-test.com"
        db.session.add(account)
        db.session.commit()

    resp = client.get(f"/admin/domains/{domain_id}/accounts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["email"] == "user@accounts-test.com"
    assert data["accounts"][0]["auth_type"] == "password"


def test_admin_domain_accounts_empty(admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "empty-accounts.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.get(f"/admin/domains/{domain_id}/accounts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["accounts"] == []


@patch("app.admin.controllers.admin.log_audit")
@patch("app.admin.controllers.admin._sync_domain_to_mail_api")
def test_admin_save_mail_api_config(mock_sync, mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "mailapi-save.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(
        f"/admin/domains/{domain_id}/mail-api-config",
        data={
            "mail_api_url": "http://mail-api:8800",
            "mail_api_key": "test-key",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        assert domain.mail_api_url == "http://mail-api:8800"
        assert domain.mail_api_key == "test-key"


def _create_customer_with_account(app, domain_id, email, auth_type="password"):
    with app.app_context():
        cust = User()
        cust.email = email
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount()
        account.customer_id = cust.id
        account.domain_id = domain_id
        account.email_address = email
        account.auth_type = auth_type
        account.username = email
        db.session.add(account)
        db.session.commit()
        return cust.id


@patch("app.admin.controllers.admin.log_audit")
@patch("app.admin.controllers.admin._mail_api_call")
def test_admin_create_customer_external(mock_mail_api, mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "ext.com"
        domain.imap_host = "imap.ext.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.ext.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post(
        "/admin/customers/new",
        data={"username": "alice", "domain_id": domain_id, "create_mode": "external"},
    )
    assert resp.status_code == 302
    mock_mail_api.assert_not_called()

    with app.app_context():
        account = CustomerAccount.query.filter_by(email_address="alice@ext.com").first()
        assert account is not None
        assert account.auth_type == "external"


@patch("app.admin.controllers.admin.log_audit")
def test_admin_reset_customer_password(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "reset.com"
        domain.imap_host = "imap.reset.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.reset.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(app, domain_id, "bob@reset.com")
    resp = client.post(f"/admin/customers/{cust_id}/reset-password")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Invitation link" in html
    assert "bob@reset.com" in html

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account is not None
        assert account.signup_token is not None
        assert account.signup_expires_at is not None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_reset_customer_password_no_account(mock_audit, admin_client, app):
    client, _ = admin_client
    cust_id = None
    with app.app_context():
        cust = User()
        cust.email = "noaccount@reset.com"
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id
        db.session.commit()

    resp = client.post(f"/admin/customers/{cust_id}/reset-password")
    assert resp.status_code == 302


@patch("app.admin.controllers.admin.log_audit")
@patch("app.admin.controllers.admin._mail_api_call")
def test_admin_set_customer_password(mock_mail_api, mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "setpw.com"
        domain.imap_host = "imap.setpw.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.setpw.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(app, domain_id, "carol@setpw.com")
    resp = client.post(
        f"/admin/customers/{cust_id}/set-password", data={"password": "newsecret123"}
    )
    assert resp.status_code == 302
    mock_mail_api.assert_called_once()

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account is not None
        assert account.auth_type == "password"
        assert account.signup_token is None
        assert account.signup_expires_at is None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_set_customer_password_empty(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "emptypw.com"
        domain.imap_host = "imap.emptypw.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.emptypw.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(app, domain_id, "dave@emptypw.com")
    resp = client.post(f"/admin/customers/{cust_id}/set-password", data={"password": ""})
    assert resp.status_code == 302


def test_admin_customers_page_shows_external_badge(admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "badge.com"
        domain.imap_host = "imap.badge.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.badge.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    _create_customer_with_account(app, domain_id, "ext@badge.com", auth_type="external")
    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "External" in html


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_customer_external_from_hosted(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "toext.com"
        domain.imap_host = "imap.toext.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.toext.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(
        app, domain_id, "hosted@toext.com", auth_type="password"
    )
    resp = client.post(f"/admin/customers/{cust_id}/toggle-external", data={"mode": "external"})
    assert resp.status_code == 302

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account is not None
        assert account.auth_type == "external"
        assert account.signup_token is None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_customer_external_to_hosted(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "tohosted.com"
        domain.imap_host = "imap.tohosted.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.tohosted.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(
        app, domain_id, "ext@tohosted.com", auth_type="external"
    )
    resp = client.post(f"/admin/customers/{cust_id}/toggle-external", data={"mode": "hosted"})
    assert resp.status_code == 302

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account is not None
        assert account.auth_type == "password"


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_customer_external_no_account(mock_audit, admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "noacc.com"
        domain.imap_host = "imap.noacc.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.noacc.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.status = "complete"
        db.session.add(domain)
        db.session.commit()

    cust_id = None
    with app.app_context():
        cust = User()
        cust.email = "noacc@noacc.com"
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id
        db.session.commit()

    resp = client.post(f"/admin/customers/{cust_id}/toggle-external", data={"mode": "external"})
    assert resp.status_code == 302

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account is not None
        assert account is not None
        assert account.auth_type == "external"


def test_admin_customers_page_shows_no_account_badge(admin_client, app):
    client, _ = admin_client
    with app.app_context():
        cust = User()
        cust.email = "nobadge@noacc.com"
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        db.session.commit()

    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "No account" in html


def test_admin_customers_page_shows_admin_row(admin_client, app):
    client, _admin_id = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.example.com"
        domain.smtp_host = "smtp.example.com"
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.commit()

    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "admin@example.com" in html
    assert "Admin</span>" in html


def test_admin_customers_page_auto_creates_account(admin_client, app):
    client, admin_id = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "example.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.example.com"
        domain.smtp_host = "smtp.example.com"
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.commit()

    client.get("/admin/customers")

    with app.app_context():
        acc = CustomerAccount.query.filter_by(customer_id=admin_id).first()
        assert acc is not None
        assert acc.email_address == "admin@example.com"
        assert acc.auth_type == "password"


def test_admin_customers_page_no_account_without_matching_domain(admin_client, app):
    client, admin_id = admin_client
    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "admin@example.com" in html
    assert "No account" in html

    with app.app_context():
        acc = CustomerAccount.query.filter_by(customer_id=admin_id).first()
        assert acc is None


def test_admin_customers_page_shows_sync_button_with_mail_api(admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "sync-test.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.sync-test.com"
        domain.smtp_host = "smtp.sync-test.com"
        domain.smtp_tls_mode = "starttls"
        domain.mail_api_url = "http://mail-api:8800"
        domain.mail_api_key = "test-key"
        db.session.add(domain)
        db.session.commit()

    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Sync accounts" in html
    assert "sync-test.com" in html


def test_admin_customers_page_no_sync_button_without_mail_api(admin_client, app):
    client, _ = admin_client
    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    assert "Sync accounts" not in resp.data.decode()


def _setup_self_hosted_domain(app):
    with app.app_context():
        domain = Domain()
        domain.name = "selfhosted.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.selfhosted.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.selfhosted.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.mail_api_url = "http://mail-api:8800"
        domain.mail_api_key = "test-key"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        cfg = DomainDnsConfig()
        cfg.domain_id = domain_id
        cfg.is_self_hosted = True
        db.session.add(cfg)
        cust = User()
        cust.email = "user@selfhosted.com"
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount()
        account.customer_id = cust.id
        account.domain_id = domain_id
        account.email_address = "user@selfhosted.com"
        account.auth_type = "password"
        account.username = "user@selfhosted.com"
        db.session.add(account)
        db.session.flush()
        account_id = account.id
        customer_id = cust.id
        db.session.commit()
    return domain_id, account_id, customer_id


@patch("app.admin.controllers.admin._mail_api_call")
def test_account_reset_password(mock_mail_api, admin_client, app):
    client, _ = admin_client
    domain_id, account_id, _ = _setup_self_hosted_domain(app)
    resp = client.post(
        f"/admin/domains/{domain_id}/accounts/{account_id}/reset-password",
        data='{"password": "newpass123"}',
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["email"] == "user@selfhosted.com"
    mock_mail_api.assert_called()
    with app.app_context():
        acc = db.session.get(CustomerAccount, account_id)
        assert acc is not None
        assert acc.auth_type == "password"
        assert acc.signup_token is None


def test_account_reset_password_not_self_hosted(admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "notself.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.notself.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.notself.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        cust = User()
        cust.email = "user@notself.com"
        cust.role = "customer"
        cust.is_active = True
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount()
        account.customer_id = cust.id
        account.domain_id = domain_id
        account.email_address = "user@notself.com"
        account.auth_type = "password"
        account.username = "user@notself.com"
        db.session.add(account)
        db.session.flush()
        account_id = account.id
        db.session.commit()
    resp = client.post(
        f"/admin/domains/{domain_id}/accounts/{account_id}/reset-password",
        data='{"password": "newpass123"}',
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert "not self-hosted" in data["error"]


def test_account_reset_password_empty(admin_client, app):
    client, _ = admin_client
    domain_id, account_id, _ = _setup_self_hosted_domain(app)
    resp = client.post(
        f"/admin/domains/{domain_id}/accounts/{account_id}/reset-password",
        data='{"password": ""}',
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert "required" in data["error"]


def test_account_login_link(admin_client, app):
    client, _ = admin_client
    domain_id, account_id, _ = _setup_self_hosted_domain(app)
    resp = client.post(
        f"/admin/domains/{domain_id}/accounts/{account_id}/login-link",
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "login_url" in data
    assert "/signup/" in data["login_url"]
    with app.app_context():
        acc = db.session.get(CustomerAccount, account_id)
        assert acc is not None
        assert acc.signup_token is not None
        assert acc.signup_expires_at is not None


@patch("app.admin.controllers.admin._mail_api_call")
def test_account_delete(mock_mail_api, admin_client, app):
    client, _ = admin_client
    domain_id, account_id, customer_id = _setup_self_hosted_domain(app)
    resp = client.post(
        f"/admin/domains/{domain_id}/accounts/{account_id}/delete",
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    mock_mail_api.assert_called()
    with app.app_context():
        acc = db.session.get(CustomerAccount, account_id)
        assert acc is None
        user = db.session.get(User, customer_id)
        assert user is None


def test_account_delete_wrong_domain(admin_client, app):
    client, _ = admin_client
    _domain_id, account_id, _ = _setup_self_hosted_domain(app)
    with app.app_context():
        domain2 = Domain()
        domain2.name = "other.com"
        domain2.is_active = True
        domain2.status = "complete"
        domain2.imap_host = "imap.other.com"
        domain2.imap_port = 993
        domain2.smtp_host = "smtp.other.com"
        domain2.smtp_port = 587
        domain2.smtp_tls_mode = "starttls"
        db.session.add(domain2)
        db.session.flush()
        domain2_id = domain2.id
        db.session.commit()
    resp = client.post(
        f"/admin/domains/{domain2_id}/accounts/{account_id}/delete",
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "does not belong" in data["error"]


def test_domain_sync_page(admin_client, app):
    client, _ = admin_client
    domain_id, _, _ = _setup_self_hosted_domain(app)
    resp = client.get(f"/admin/domains/{domain_id}/sync")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Check sync status" in html


def test_domain_sync_redirects_non_self_hosted(admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain()
        domain.name = "notself2.com"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.notself2.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.notself2.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()
    resp = client.get(f"/admin/domains/{domain_id}/sync")
    assert resp.status_code == 302
    assert "/review/accounts" in resp.headers["Location"]


def test_review_accounts_page(admin_client, app):
    client, _ = admin_client
    domain_id, _, _ = _setup_self_hosted_domain(app)
    resp = client.get(f"/admin/domains/{domain_id}/review/accounts")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "user@selfhosted.com" in html
    assert "Reset password" in html
    assert "Login link" in html
    assert "Delete" in html
    assert "Sync accounts" in html


@patch("app.admin.controllers.admin.log_audit")
def test_admin_save_dav_config_matrix_mas_and_visibility(mock_audit, admin_client, app):
    """dav-config saves MAS fields and the chat visibility allowlist (U25.17)."""
    client, _ = admin_client
    domain_id = None
    other_id = None
    with app.app_context():
        domain = Domain()
        domain.name = "mas-config.test"
        domain.is_active = True
        domain.status = "complete"
        domain.imap_host = "imap.test.com"
        domain.imap_port = 993
        domain.smtp_host = "smtp.test.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        other = Domain()
        other.name = "other-mas.test"
        other.is_active = True
        other.status = "complete"
        other.imap_host = "imap.other.test"
        other.imap_port = 993
        other.smtp_host = "smtp.other.test"
        other.smtp_port = 587
        other.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.add(other)
        db.session.flush()
        domain_id, other_id = domain.id, other.id
        db.session.commit()

    resp = client.post(
        f"/admin/domains/{domain_id}/dav-config",
        data={
            "matrix_host": "synapse",
            "matrix_port": "8008",
            "matrix_mas_url": "http://mas:8080/",
            "matrix_mas_client_id": "01MAS00000000000000000000A",
            "matrix_mas_client_secret": "secret",
            # Valid id + junk entries: only valid ids are kept.
            "chat_visible_domain_ids": [str(other_id), "9999", "not-a-number"],
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    with app.app_context():
        saved = db.session.get(Domain, domain_id)
        assert saved is not None
        assert saved.matrix_mas_url == "http://mas:8080"
        assert saved.matrix_mas_client_id == "01MAS00000000000000000000A"
        assert saved.chat_visible_domain_ids == [other_id]
    mock_audit.assert_called_once()
