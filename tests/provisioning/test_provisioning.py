import pytest
from unittest.mock import patch, MagicMock


PROVISIONING_KEY = "test-provisioning-key"


@pytest.fixture()
def provision_headers():
    return {"Authorization": f"Bearer {PROVISIONING_KEY}", "Content-Type": "application/json"}


@pytest.fixture()
def mock_mail_client():
    client = MagicMock()
    client.is_available.return_value = True
    client.check_user.return_value = False
    client.add_domain.return_value = {"status": "ok", "domain": "example.com"}
    client.add_user.return_value = {"status": "ok", "email": "user@example.com"}
    client.remove_user.return_value = {"status": "ok"}
    client.list_users.return_value = [{"email": "user@example.com"}]
    client.generate_dkim_key.return_value = {
        "selector": "default",
        "public_key": "TESTKEY",
        "txt_record": "v=DKIM1; k=rsa; p=TESTKEY",
    }
    client.get_dkim_key.return_value = {
        "selector": "default",
        "public_key": "TESTKEY",
        "txt_record": "v=DKIM1; k=rsa; p=TESTKEY",
    }
    client.set_quota.return_value = {"status": "ok"}
    client.set_sending_limit.return_value = {"status": "ok"}
    client.delete_sending_limit.return_value = {"status": "ok"}
    return client


@pytest.fixture(autouse=True)
def _setup_provisioning_key(app):
    app.config["PROVISIONING_API_KEY"] = PROVISIONING_KEY


def _unauth(client):
    return {"Authorization": "Bearer wrong-key", "Content-Type": "application/json"}


def test_check_availability_unauthorized(client):
    resp = client.post("/api/provision/check-availability", json={"email": "a@b.com"})
    assert resp.status_code == 401


def test_check_availability_invalid_email(client, provision_headers):
    resp = client.post("/api/provision/check-availability", json={"email": ""}, headers=provision_headers)
    assert resp.status_code == 400


@patch("app.provisioning.controllers._get_mail_client")
def test_check_availability_available(mock_get, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    mock_mail_client.check_user.return_value = False
    resp = client.post("/api/provision/check-availability", json={"email": "user@example.com"}, headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is True


@patch("app.provisioning.controllers._get_mail_client")
def test_check_availability_taken(mock_get, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    mock_mail_client.check_user.return_value = True
    resp = client.post("/api/provision/check-availability", json={"email": "user@example.com"}, headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is False


def test_check_availability_local_user_exists(app, client, provision_headers):
    from app.shared.db import db
    from app.shared.models.core import User

    with app.app_context():
        user = User(email="admin@example.com", role="admin", is_active=True)
        user.password_hash = "x"
        db.session.add(user)
        db.session.commit()

    resp = client.post("/api/provision/check-availability", json={"email": "admin@example.com"}, headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is False


def test_check_availability_local_user_case_insensitive(app, client, provision_headers):
    from app.shared.db import db
    from app.shared.models.core import User

    with app.app_context():
        user = User(email="admin@example.com", role="admin", is_active=True)
        user.password_hash = "x"
        db.session.add(user)
        db.session.commit()

    resp = client.post("/api/provision/check-availability", json={"email": "ADMIN@EXAMPLE.COM"}, headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is False


def test_check_availability_no_at_sign(client, provision_headers):
    resp = client.post("/api/provision/check-availability", json={"email": "noatsign"}, headers=provision_headers)
    assert resp.status_code == 400


@patch("app.provisioning.controllers._get_mail_client")
def test_check_availability_service_error(mock_get, client, provision_headers):
    mock_get.side_effect = RuntimeError("Mail API is not configured")
    resp = client.post("/api/provision/check-availability", json={"email": "user@example.com"}, headers=provision_headers)
    assert resp.status_code == 503


@patch("app.provisioning.controllers._get_mail_client")
def test_create_domain(mock_get, app, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    resp = client.post("/api/provision/create-domain", json={"domain": "example.com"}, headers=provision_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["created"] is True
    from app.shared.models.core import Domain
    from app.shared.db import db
    with app.app_context():
        domain = Domain.query.filter_by(name="example.com").first()
        assert domain is not None
        assert domain.status == "complete"
        assert domain.imap_host == "dovecot"


@patch("app.provisioning.controllers._get_mail_client")
def test_create_domain_with_platform_config(mock_get, app, client, provision_headers, mock_mail_client):
    from app.shared.models.core import PlatformServiceConfig, Domain, DomainDnsConfig
    from app.shared.db import db
    mock_get.return_value = mock_mail_client
    app.config["APP_ENV"] = "production"
    with app.app_context():
        svc = PlatformServiceConfig(
            imap_host="mail.example.com",
            imap_port=993,
            smtp_host="mail.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
            carddav_host="radicale.example.com",
            caldav_host="radicale.example.com",
        )
        db.session.add(svc)
        db.session.commit()
    resp = client.post("/api/provision/create-domain", json={"domain": "newdomain.com"}, headers=provision_headers)
    assert resp.status_code == 201
    with app.app_context():
        domain = Domain.query.filter_by(name="newdomain.com").first()
        assert domain is not None
        assert domain.status == "complete"
        assert domain.imap_host == "mail.example.com"
        assert domain.smtp_host == "mail.example.com"
        assert domain.carddav_host == "radicale.example.com"
        assert domain.caldav_host == "radicale.example.com"
        dns_config = DomainDnsConfig.query.filter_by(domain_id=domain.id).first()
        assert dns_config is not None
        assert dns_config.is_self_hosted is True
    app.config["APP_ENV"] = "development"


@patch("app.provisioning.controllers._get_mail_client")
def test_create_domain_idempotent(mock_get, app, client, provision_headers, mock_mail_client):
    from app.shared.models.core import Domain
    from app.shared.db import db
    mock_get.return_value = mock_mail_client
    resp1 = client.post("/api/provision/create-domain", json={"domain": "example.com"}, headers=provision_headers)
    assert resp1.status_code == 201
    resp2 = client.post("/api/provision/create-domain", json={"domain": "example.com"}, headers=provision_headers)
    assert resp2.status_code == 201
    with app.app_context():
        assert Domain.query.filter_by(name="example.com").count() == 1


@patch("app.provisioning.controllers._get_mail_client")
def test_create_domain_production_no_platform_config(mock_get, app, client, provision_headers, mock_mail_client):
    from app.shared.models.core import Domain
    from app.shared.db import db
    mock_get.return_value = mock_mail_client
    app.config["APP_ENV"] = "production"
    resp = client.post("/api/provision/create-domain", json={"domain": "noplat.com"}, headers=provision_headers)
    assert resp.status_code == 201
    with app.app_context():
        domain = Domain.query.filter_by(name="noplat.com").first()
        assert domain is not None
        assert domain.status == "review"
        assert domain.imap_host == ""
    app.config["APP_ENV"] = "development"


def test_create_domain_missing(client, provision_headers):
    resp = client.post("/api/provision/create-domain", json={}, headers=provision_headers)
    assert resp.status_code == 400


@patch("app.provisioning.controllers._get_mail_client")
def test_create_mailbox(mock_get, app, client, provision_headers, mock_mail_client):
    from app.shared.models.core import User, CustomerAccount, Domain
    from app.shared.db import db
    mock_get.return_value = mock_mail_client
    with app.app_context():
        domain = Domain(
            name="example.com",
            is_active=True,
            status="complete",
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.commit()
    resp = client.post("/api/provision/create-mailbox", json={
        "email": "user@example.com",
        "password": "secret123",
        "domain": "example.com",
        "quota_bytes": 5368709120,
        "max_emails_per_day": 200,
    }, headers=provision_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["created"] is True
    assert data["email"] == "user@example.com"
    mock_mail_client.add_domain.assert_called_once_with("example.com")
    mock_mail_client.add_user.assert_called_once_with("user@example.com", "secret123", quota_bytes=5368709120)
    mock_mail_client.set_sending_limit.assert_called_once_with("user@example.com", 200)
    with app.app_context():
        user = User.query.filter_by(email="user@example.com").first()
        assert user is not None
        assert user.role == "customer"
        assert user.is_active is True
        account = CustomerAccount.query.filter_by(email_address="user@example.com").first()
        assert account is not None
        assert account.domain_id is not None
        assert account.auth_type == "password"


@patch("app.provisioning.controllers._get_mail_client")
def test_create_mailbox_without_domain_in_db(mock_get, app, client, provision_headers, mock_mail_client):
    from app.shared.models.core import User
    mock_get.return_value = mock_mail_client
    resp = client.post("/api/provision/create-mailbox", json={
        "email": "user@unknowndomain.com",
        "password": "secret123",
    }, headers=provision_headers)
    assert resp.status_code == 201
    with app.app_context():
        user = User.query.filter_by(email="user@unknowndomain.com").first()
        assert user is not None
        assert user.role == "customer"


@patch("app.provisioning.controllers._get_mail_client")
def test_create_mailbox_idempotent_user(mock_get, app, client, provision_headers, mock_mail_client):
    from app.shared.models.core import User
    from app.shared.db import db
    mock_get.return_value = mock_mail_client
    with app.app_context():
        user = User(email="existing@example.com", role="customer", is_active=True)
        user.password_hash = "x"
        db.session.add(user)
        db.session.commit()
    resp = client.post("/api/provision/create-mailbox", json={
        "email": "existing@example.com",
        "password": "secret123",
    }, headers=provision_headers)
    assert resp.status_code == 201
    with app.app_context():
        assert User.query.filter_by(email="existing@example.com").count() == 1


def test_create_mailbox_missing_fields(client, provision_headers):
    resp = client.post("/api/provision/create-mailbox", json={}, headers=provision_headers)
    assert resp.status_code == 400


@patch("app.provisioning.controllers._get_mail_client")
def test_delete_mailbox(mock_get, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    resp = client.delete("/api/provision/mailbox/user@example.com", headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["deleted"] is True
    mock_mail_client.remove_user.assert_called_once_with("user@example.com")


@patch("app.provisioning.controllers._get_mail_client")
def test_list_users(mock_get, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    resp = client.get("/api/provision/users/example.com", headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert "data" in data


@patch("app.provisioning.controllers._get_mail_client")
def test_generate_dkim(mock_get, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    resp = client.post("/api/provision/generate-dkim", json={"domain": "example.com"}, headers=provision_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["selector"] == "default"
    assert "public_key" in data


def test_generate_dkim_missing_domain(client, provision_headers):
    resp = client.post("/api/provision/generate-dkim", json={}, headers=provision_headers)
    assert resp.status_code == 400


@patch("app.provisioning.controllers._get_mail_client")
def test_update_quota(mock_get, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    resp = client.put("/api/provision/mailbox/user@example.com/quota", json={"quota_bytes": 10737418240}, headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["updated"] is True


def test_update_quota_invalid(client, provision_headers):
    resp = client.put("/api/provision/mailbox/user@example.com/quota", json={"quota_bytes": -1}, headers=provision_headers)
    assert resp.status_code == 400


@patch("app.provisioning.controllers._get_mail_client")
@patch("app.admin.services.dns_checks.run_all_dns_checks")
def test_validate_dns(mock_dns, mock_get, app, client, provision_headers, mock_mail_client):
    from app.shared.models.core import PlatformDnsConfig
    mock_get.return_value = mock_mail_client
    mock_dns.return_value = {
        "mx": {"status": "ok"},
        "spf": {"status": "ok"},
        "dkim": {"status": "ok"},
        "dmarc": {"status": "ok"},
    }
    with app.app_context():
        config = PlatformDnsConfig(mx_hostname="mail.example.com", mx_priority=10)
        from app.shared.db import db
        db.session.add(config)
        db.session.commit()

    resp = client.post("/api/provision/validate-dns/example.com", headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert "mx" in data
    assert "spf" in data
    assert "dkim" in data
    assert "dmarc" in data


@patch("app.provisioning.controllers._get_mail_client")
@patch("app.admin.services.dns_checks._query_record_at_ns", return_value=["locoroo-verify=abc123"])
@patch("app.admin.services.dns_checks._resolve_ns_ips", return_value=["1.2.3.4"])
@patch("app.admin.services.dns_checks._get_authoritative_nameservers", return_value=["ns1.example.com"])
def test_validate_ownership(mock_ns, mock_resolve, mock_query, mock_get, client, provision_headers, mock_mail_client):
    mock_get.return_value = mock_mail_client
    resp = client.post("/api/provision/validate-ownership/example.com", json={
        "expected_value": "locoroo-verify=abc123",
    }, headers=provision_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert "verified" in data
    assert "found" in data


def test_validate_ownership_missing_value(client, provision_headers):
    resp = client.post("/api/provision/validate-ownership/example.com", json={}, headers=provision_headers)
    assert resp.status_code == 400
