import pytest
from unittest.mock import patch, MagicMock

from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import User, Domain, CustomerAccount, DomainDnsConfig


def test_admin_login_page(client, app, _clean_db):
    with app.app_context():
        user = User(email="admin@example.com", role="admin", is_active=True, password_hash=generate_password_hash("admin123"))
        db.session.add(user)
        db.session.commit()
    resp = client.get("/admin/login")
    assert resp.status_code == 200


@patch("app.admin.controllers.auth.log_audit")
@patch("app.admin.controllers.auth.clear_failed_login")
@patch("app.admin.controllers.auth.is_locked", return_value=False)
def test_admin_login_post_success(mock_locked, mock_clear, mock_audit, app, client, _clean_db):
    with app.app_context():
        user = User(
            email="admin@example.com",
            role="admin",
            is_active=True,
            password_hash=generate_password_hash("admin123"),
        )
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
        domain = Domain(
            name="example.com",
            is_active=True,
            status="complete",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()

        dns_cfg = DomainDnsConfig(
            domain_id=domain.id,
            is_self_hosted=True,
            dkim_selector="default",
            dmarc_policy="none",
        )
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
        domain = Domain(
            name="connected.example.com",
            is_active=True,
            status="complete",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            carddav_host="carddav.example.com",
            carddav_port=5232,
            caldav_host="caldav.example.com",
            caldav_port=5232,
        )
        db.session.add(domain)
        db.session.commit()

    resp = client.get("/admin/domains")
    assert resp.status_code == 200
    assert b"connected.example.com" in resp.data


def test_admin_domains_page_with_inactive_domain(admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain(
            name="inactive.example.com",
            is_active=False,
            status="draft",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
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
    return_value={"imap_primary": None, "smtp_primary": None, "imap_candidates": [], "smtp_candidates": []},
)
def test_admin_create_domain(mock_discover, mock_audit, admin_client):
    client, _ = admin_client
    resp = client.post("/admin/domains/new", data={"name": "test.com"})
    assert resp.status_code == 302


@patch("app.admin.controllers.admin.log_audit")
@patch("app.admin.controllers.admin.generate_password_hash", return_value="hashed")
def test_admin_create_manager(mock_hash, mock_audit, admin_client):
    client, _ = admin_client
    resp = client.post("/admin/managers/new", data={"email": "mgr@example.com", "password": "secret"})
    assert resp.status_code == 302


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="example.com",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()
    resp = client.post("/admin/customers/new", data={"username": "cust", "domain_id": domain_id, "create_mode": "invite"})
    assert resp.status_code == 200


@patch("app.admin.controllers.admin.log_audit")
def test_admin_create_customer_password_includes_login_link(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="example.com",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    with patch("app.admin.services.mail_server.get_mail_client", return_value=MagicMock()):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "newuser", "domain_id": str(domain_id), "password": "secret123", "create_mode": "password"},
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
        domain = Domain(
            name="example.com",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_client = MagicMock()
    mock_client.check_user.return_value = True
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_client):
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
        domain = Domain(
            name="example.com",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
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
        domain = Domain(
            name="example.com",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        existing = User(email="dup@example.com", role="customer")
        db.session.add(existing)
        db.session.commit()

    mock_client = MagicMock()
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_client):
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
        domain = Domain(
            name="example.com",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        existing = User(email="dup@example.com", role="customer")
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
        domain = Domain(
            name="example.com",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    mock_client = MagicMock()
    mock_client.add_user.side_effect = Exception("User already exists")
    with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_client):
        resp = client.post(
            "/admin/customers/new",
            data={"username": "failuser", "domain_id": str(domain_id), "password": "secret", "create_mode": "password"},
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
        domain = Domain(
            name="toggle.com",
            is_active=True,
            status="complete",
            imap_host="imap.toggle.com",
            imap_port=993,
            smtp_host="smtp.toggle.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
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
        cust = User(email="toggle-cust@example.com", role="customer", is_active=True)
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
        domain = Domain(
            name="update.com",
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
    return_value={"imap_primary": None, "smtp_primary": None, "imap_candidates": [], "smtp_candidates": []},
)
def test_admin_review_domain_page(mock_discover, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="review.com",
            is_active=True,
            status="review",
            imap_host="",
            imap_port=993,
            smtp_host="",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.get(f"/admin/domains/{domain_id}/review")
    assert resp.status_code == 302
    assert f"/admin/domains/{domain_id}/review/mail" in resp.headers["Location"]

    resp = client.get(f"/admin/domains/{domain_id}/review/mail")
    assert resp.status_code == 200


@patch("app.admin.controllers.admin.log_audit")
def test_admin_update_domain_carddav(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="carddav-update.com",
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
        assert domain.carddav_host == "dav.test.com"
        assert domain.carddav_port == 5232
        assert domain.carddav_use_tls is True


@patch("app.admin.controllers.admin.log_audit")
def test_admin_update_domain_carddav_clear(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="carddav-clear.com",
            is_active=True,
            status="complete",
            imap_host="imap.test.com",
            imap_port=993,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            carddav_host="dav.test.com",
            carddav_port=5232,
            carddav_use_tls=True,
        )
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
        assert domain.carddav_host is None
        assert domain.carddav_use_tls is False


@patch("app.admin.controllers.admin.log_audit")
@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={"imap_primary": None, "smtp_primary": None, "imap_candidates": [], "smtp_candidates": []},
)
def test_admin_review_domain_saves_carddav(mock_discover, mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="carddav-review.com",
            is_active=True,
            status="review",
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
        assert domain.carddav_host == "carddav.test.com"
        assert domain.carddav_port == 8443
        assert domain.carddav_use_tls is True


@patch(
    "app.admin.controllers.admin.discover_domain_settings",
    return_value={"imap_primary": None, "smtp_primary": None, "imap_candidates": [], "smtp_candidates": []},
)
def test_admin_review_domain_page_shows_carddav_fields(mock_discover, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="carddav-ui.com",
            is_active=True,
            status="review",
            imap_host="",
            imap_port=993,
            smtp_host="",
            smtp_port=587,
            smtp_tls_mode="starttls",
            carddav_host="existing-dav.com",
            carddav_port=5232,
        )
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
        domain = Domain(
            name="health.example.com",
            is_active=True,
            status="complete",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
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
        domain = Domain(
            name="mail-save.com",
            is_active=True,
            status="review",
            imap_host="old-imap.com",
            imap_port=993,
            smtp_host="old-smtp.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
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
        assert domain.imap_host == "new-imap.com"
        assert domain.smtp_port == 465
        assert domain.smtp_tls_mode == "tls"


@patch("app.admin.controllers.admin.log_audit")
def test_admin_save_dav_config(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="dav-save.com",
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
        assert domain.caldav_host == "caldav.new.com"
        assert domain.caldav_port == 8443
        assert domain.caldav_use_tls is True
        assert domain.carddav_host == "carddav.new.com"
        assert domain.carddav_port == 8444


def test_admin_domain_accounts_json(admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="accounts-test.com",
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
        cust = User(email="user@accounts-test.com", role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount(
            customer_id=cust.id,
            domain_id=domain_id,
            email_address="user@accounts-test.com",
            auth_type="password",
            username="user@accounts-test.com",
        )
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
        domain = Domain(
            name="empty-accounts.com",
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
        domain = Domain(
            name="mailapi-save.com",
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
        assert domain.mail_api_url == "http://mail-api:8800"
        assert domain.mail_api_key == "test-key"


def _create_customer_with_account(app, domain_id, email, auth_type="password"):
    with app.app_context():
        cust = User(email=email, role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount(
            customer_id=cust.id,
            domain_id=domain_id,
            email_address=email,
            auth_type=auth_type,
            username=email,
        )
        db.session.add(account)
        db.session.commit()
        return cust.id


@patch("app.admin.controllers.admin.log_audit")
@patch("app.admin.controllers.admin._mail_api_call")
def test_admin_create_customer_external(mock_mail_api, mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="ext.com",
            imap_host="imap.ext.com",
            imap_port=993,
            smtp_host="smtp.ext.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    resp = client.post("/admin/customers/new", data={"username": "alice", "domain_id": domain_id, "create_mode": "external"})
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
        domain = Domain(
            name="reset.com",
            imap_host="imap.reset.com",
            imap_port=993,
            smtp_host="smtp.reset.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
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
        assert account.signup_token is not None
        assert account.signup_expires_at is not None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_reset_customer_password_no_account(mock_audit, admin_client, app):
    client, _ = admin_client
    cust_id = None
    with app.app_context():
        cust = User(email="noaccount@reset.com", role="customer", is_active=True)
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
        domain = Domain(
            name="setpw.com",
            imap_host="imap.setpw.com",
            imap_port=993,
            smtp_host="smtp.setpw.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(app, domain_id, "carol@setpw.com")
    resp = client.post(f"/admin/customers/{cust_id}/set-password", data={"password": "newsecret123"})
    assert resp.status_code == 302
    mock_mail_api.assert_called_once()

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account.auth_type == "password"
        assert account.signup_token is None
        assert account.signup_expires_at is None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_set_customer_password_empty(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="emptypw.com",
            imap_host="imap.emptypw.com",
            imap_port=993,
            smtp_host="smtp.emptypw.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
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
        domain = Domain(
            name="badge.com",
            imap_host="imap.badge.com",
            imap_port=993,
            smtp_host="smtp.badge.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
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
        domain = Domain(
            name="toext.com",
            imap_host="imap.toext.com",
            imap_port=993,
            smtp_host="smtp.toext.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(app, domain_id, "hosted@toext.com", auth_type="password")
    resp = client.post(f"/admin/customers/{cust_id}/toggle-external", data={"mode": "external"})
    assert resp.status_code == 302

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account.auth_type == "external"
        assert account.signup_token is None


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_customer_external_to_hosted(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = None
    with app.app_context():
        domain = Domain(
            name="tohosted.com",
            imap_host="imap.tohosted.com",
            imap_port=993,
            smtp_host="smtp.tohosted.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()

    cust_id = _create_customer_with_account(app, domain_id, "ext@tohosted.com", auth_type="external")
    resp = client.post(f"/admin/customers/{cust_id}/toggle-external", data={"mode": "hosted"})
    assert resp.status_code == 302

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account.auth_type == "password"


@patch("app.admin.controllers.admin.log_audit")
def test_admin_toggle_customer_external_no_account(mock_audit, admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain(
            name="noacc.com",
            imap_host="imap.noacc.com",
            imap_port=993,
            smtp_host="smtp.noacc.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.commit()

    cust_id = None
    with app.app_context():
        cust = User(email="noacc@noacc.com", role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id
        db.session.commit()

    resp = client.post(f"/admin/customers/{cust_id}/toggle-external", data={"mode": "external"})
    assert resp.status_code == 302

    with app.app_context():
        account = CustomerAccount.query.filter_by(customer_id=cust_id).first()
        assert account is not None
        assert account.auth_type == "external"


def test_admin_customers_page_shows_no_account_badge(admin_client, app):
    client, _ = admin_client
    cust_id = None
    with app.app_context():
        cust = User(email="nobadge@noacc.com", role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id
        db.session.commit()

    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "No account" in html


def test_admin_customers_page_shows_admin_row(admin_client, app):
    client, admin_id = admin_client
    with app.app_context():
        domain = Domain(
            name="example.com", is_active=True, status="complete",
            imap_host="imap.example.com", smtp_host="smtp.example.com",
            smtp_tls_mode="starttls",
        )
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
        domain = Domain(
            name="example.com", is_active=True, status="complete",
            imap_host="imap.example.com", smtp_host="smtp.example.com",
            smtp_tls_mode="starttls",
        )
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
        domain = Domain(
            name="sync-test.com",
            is_active=True,
            status="complete",
            imap_host="imap.sync-test.com",
            smtp_host="smtp.sync-test.com",
            smtp_tls_mode="starttls",
            mail_api_url="http://mail-api:8800",
            mail_api_key="test-key",
        )
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
        domain = Domain(
            name="selfhosted.com",
            is_active=True,
            status="complete",
            imap_host="imap.selfhosted.com",
            imap_port=993,
            smtp_host="smtp.selfhosted.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            mail_api_url="http://mail-api:8800",
            mail_api_key="test-key",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        cfg = DomainDnsConfig(domain_id=domain_id, is_self_hosted=True)
        db.session.add(cfg)
        cust = User(email="user@selfhosted.com", role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount(
            customer_id=cust.id,
            domain_id=domain_id,
            email_address="user@selfhosted.com",
            auth_type="password",
            username="user@selfhosted.com",
        )
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
        assert acc.auth_type == "password"
        assert acc.signup_token is None


def test_account_reset_password_not_self_hosted(admin_client, app):
    client, _ = admin_client
    with app.app_context():
        domain = Domain(
            name="notself.com",
            is_active=True,
            status="complete",
            imap_host="imap.notself.com",
            imap_port=993,
            smtp_host="smtp.notself.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        cust = User(email="user@notself.com", role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        account = CustomerAccount(
            customer_id=cust.id,
            domain_id=domain_id,
            email_address="user@notself.com",
            auth_type="password",
            username="user@notself.com",
        )
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
    domain_id, account_id, _ = _setup_self_hosted_domain(app)
    with app.app_context():
        domain2 = Domain(
            name="other.com",
            is_active=True,
            status="complete",
            imap_host="imap.other.com",
            imap_port=993,
            smtp_host="smtp.other.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
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
        domain = Domain(
            name="notself2.com",
            is_active=True,
            status="complete",
            imap_host="imap.notself2.com",
            imap_port=993,
            smtp_host="smtp.notself2.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
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
