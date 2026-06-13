from unittest.mock import patch, MagicMock

from app.shared.db import db
from app.shared.models.core import Domain, PlatformDnsConfig, PlatformServiceConfig, DomainDnsConfig


def _setup_admin(client, app):
    from werkzeug.security import generate_password_hash
    from app.shared.models.core import User
    with app.app_context():
        user = User(
            email="admin@test.com",
            role="admin",
            is_active=True,
            password_hash=generate_password_hash("pw"),
        )
        db.session.add(user)
        db.session.flush()
        uid = user.id
        db.session.commit()
    with client.session_transaction() as sess:
        sess["role"] = "admin"
        sess["user_id"] = uid
    return uid


class TestPlatformDnsPage:
    def test_renders_empty(self, app, client, _clean_db):
        _setup_admin(client, app)
        resp = client.get("/admin/platform-dns")
        assert resp.status_code == 200
        assert b"Platform Configuration" in resp.data

    def test_renders_with_entries(self, app, client, _clean_db):
        _setup_admin(client, app)
        with app.app_context():
            db.session.add(PlatformDnsConfig(mx_hostname="mx.example.com", mx_priority=10))
            db.session.add(PlatformDnsConfig(mx_hostname="mx2.example.com", mx_priority=20))
            db.session.commit()
        resp = client.get("/admin/platform-dns")
        assert resp.status_code == 200
        assert b"mx.example.com" in resp.data
        assert b"mx2.example.com" in resp.data

    def test_requires_admin(self, app, client, _clean_db):
        resp = client.get("/admin/platform-dns")
        assert resp.status_code == 302


class TestSavePlatformDns:
    def test_save_single_mx(self, app, client, _clean_db):
        _setup_admin(client, app)
        resp = client.post("/admin/platform-dns/save", data={
            "dkim_selector": "default",
            "mx_hostnames": "mx.example.com",
            "mx_priorities": "10",
        }, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            entries = PlatformDnsConfig.query.order_by(PlatformDnsConfig.mx_priority).all()
            assert len(entries) == 1
            assert entries[0].mx_hostname == "mx.example.com"
            assert entries[0].mx_priority == 10

    def test_save_multiple_mx(self, app, client, _clean_db):
        _setup_admin(client, app)
        resp = client.post("/admin/platform-dns/save", data={
            "dkim_selector": "myselector",
            "mx_hostnames": "mx1.example.com\nmx2.example.com",
            "mx_priorities": "10\n20",
        }, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            entries = PlatformDnsConfig.query.order_by(PlatformDnsConfig.mx_priority).all()
            assert len(entries) == 2
            assert entries[0].mx_hostname == "mx1.example.com"
            assert entries[0].mx_priority == 10
            assert entries[1].mx_hostname == "mx2.example.com"
            assert entries[1].mx_priority == 20

    def test_save_replaces_existing(self, app, client, _clean_db):
        _setup_admin(client, app)
        with app.app_context():
            db.session.add(PlatformDnsConfig(mx_hostname="old.example.com", mx_priority=10))
            db.session.commit()
        client.post("/admin/platform-dns/save", data={
            "mx_hostnames": "new.example.com",
            "mx_priorities": "5",
        }, follow_redirects=True)
        with app.app_context():
            entries = PlatformDnsConfig.query.all()
            assert len(entries) == 1
            assert entries[0].mx_hostname == "new.example.com"

    def test_auto_assigns_priority(self, app, client, _clean_db):
        _setup_admin(client, app)
        client.post("/admin/platform-dns/save", data={
            "dkim_selector": "default",
            "mx_hostnames": "mx1.example.com\nmx2.example.com",
            "mx_priorities": "10",
        }, follow_redirects=True)
        with app.app_context():
            entries = PlatformDnsConfig.query.order_by(PlatformDnsConfig.mx_priority).all()
            assert len(entries) == 2
            assert entries[0].mx_priority == 10
            assert entries[1].mx_priority == 20


class TestValidatePlatformDns:
    @patch("app.admin.services.dns_checks.validate_mx_hostname")
    def test_validate_success(self, mock_validate, app, client, _clean_db):
        _setup_admin(client, app)
        mock_validate.return_value = {
            "hostname": "mx.example.com",
            "resolves": True,
            "ips": ["1.2.3.4"],
            "port_25_reachable": True,
            "valid": True,
        }
        resp = client.post("/admin/platform-dns/validate",
                           json={"hostname": "mx.example.com"},
                           content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["result"]["valid"] is True

    @patch("app.admin.services.dns_checks.validate_mx_hostname")
    def test_validate_does_not_resolve(self, mock_validate, app, client, _clean_db):
        _setup_admin(client, app)
        mock_validate.return_value = {
            "hostname": "bad.example.com",
            "resolves": False,
            "ips": [],
            "port_25_reachable": False,
            "valid": False,
        }
        resp = client.post("/admin/platform-dns/validate",
                           json={"hostname": "bad.example.com"},
                           content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["result"]["valid"] is False

    def test_validate_empty_hostname(self, app, client, _clean_db):
        _setup_admin(client, app)
        resp = client.post("/admin/platform-dns/validate",
                           json={"hostname": ""},
                           content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False


class TestSelfHostedToggle:
    def _setup_domain(self, app):
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
                mail_api_url="http://mail-api:8800",
                mail_api_key="test-key",
            )
            db.session.add(domain)
            db.session.flush()
            domain_id = domain.id
            db.session.commit()
        return domain_id

    def test_enable_self_hosted(self, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_domain(app)
        resp = client.post(f"/admin/domains/{domain_id}/self-hosted", data={
            "is_self_hosted": "1",
            "dmarc_policy": "none",
            "dmarc_rua": "dmarc@example.com",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        with app.app_context():
            cfg = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
            assert cfg is not None
            assert cfg.is_self_hosted is True
            assert cfg.dmarc_policy == "none"
            assert cfg.dmarc_rua == "dmarc@example.com"

    def test_disable_self_hosted(self, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_domain(app)
        with app.app_context():
            cfg = DomainDnsConfig(domain_id=domain_id, is_self_hosted=True)
            db.session.add(cfg)
            db.session.commit()
        resp = client.post(f"/admin/domains/{domain_id}/self-hosted", data={
            "is_self_hosted": "0",
            "dmarc_policy": "none",
            "dmarc_rua": "",
        })
        assert resp.status_code == 200
        with app.app_context():
            cfg = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
            assert cfg.is_self_hosted is False

    def test_requires_mail_api(self, app, client, _clean_db):
        _setup_admin(client, app)
        with app.app_context():
            domain = Domain(
                name="noapi.com",
                is_active=True,
                status="complete",
                imap_host="imap.noapi.com",
                imap_port=993,
                imap_tls=True,
                smtp_host="smtp.noapi.com",
                smtp_port=587,
                smtp_tls_mode="starttls",
            )
            db.session.add(domain)
            db.session.flush()
            domain_id = domain.id
            db.session.commit()
        resp = client.post(f"/admin/domains/{domain_id}/self-hosted", data={
            "is_self_hosted": "1",
            "dmarc_policy": "none",
            "dmarc_rua": "",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "Mail API" in data["error"]

    def test_dmarc_policy_default(self, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_domain(app)
        resp = client.post(f"/admin/domains/{domain_id}/self-hosted", data={
            "is_self_hosted": "1",
            "dmarc_policy": "invalid_policy",
            "dmarc_rua": "",
        })
        assert resp.status_code == 200
        with app.app_context():
            cfg = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
            assert cfg.dmarc_policy == "none"


class TestDnsCheckEndpoint:
    def _setup_self_hosted(self, app, client):
        _setup_admin(client, app)
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
                mail_api_url="http://mail-api:8800",
                mail_api_key="test-key",
            )
            db.session.add(domain)
            db.session.flush()
            domain_id = domain.id
            cfg = DomainDnsConfig(domain_id=domain_id, is_self_hosted=True, dmarc_policy="none", dmarc_rua="dmarc@example.com")
            db.session.add(cfg)
            db.session.add(PlatformDnsConfig(mx_hostname="mx.example.com", mx_priority=10))
            db.session.commit()
        return domain_id

    def test_rejects_non_self_hosted(self, app, client, _clean_db):
        _setup_admin(client, app)
        with app.app_context():
            domain = Domain(
                name="notself.com",
                is_active=True,
                status="complete",
                imap_host="imap.notself.com",
                imap_port=993,
                imap_tls=True,
                smtp_host="smtp.notself.com",
                smtp_port=587,
                smtp_tls_mode="starttls",
            )
            db.session.add(domain)
            db.session.flush()
            domain_id = domain.id
            db.session.commit()
        resp = client.post(f"/admin/domains/{domain_id}/dns-check")
        assert resp.status_code == 400

    @patch("app.admin.services.dns_checks.run_all_dns_checks")
    @patch("app.admin.services.mail_server.get_mail_client_for_domain")
    def test_returns_check_results(self, mock_client, mock_checks, app, client, _clean_db):
        domain_id = self._setup_self_hosted(app, client)
        mock_client.return_value = MagicMock()
        mock_client.return_value.get_dkim_key.return_value = {"public_key": "ABC123"}
        mock_checks.return_value = {
            "mx": {"status": "verified", "expected": "@  IN  MX  10  mx.example.com.", "found": ["10 mx.example.com"], "nameservers_checked": 2, "nameservers_ok": 2, "details": "2/2 NS OK", "instructions": ""},
            "spf": {"status": "not_configured", "expected": "v=spf1 mx ~all", "found": None, "nameservers_checked": 2, "nameservers_ok": 0, "details": "0/2 NS OK", "instructions": "Add a TXT record"},
            "dkim": {"status": "propagating", "expected": "v=DKIM1; k=rsa; p=ABC123", "found": ["v=DKIM1; k=rsa; p=ABC123"], "nameservers_checked": 2, "nameservers_ok": 1, "details": "1/2 NS OK", "instructions": "DNS propagation"},
            "dmarc": {"status": "verified", "expected": "v=DMARC1; p=none; rua=mailto:dmarc@example.com", "found": ["v=DMARC1; p=none; rua=mailto:dmarc@example.com"], "nameservers_checked": 2, "nameservers_ok": 2, "details": "2/2 NS OK", "instructions": ""},
        }
        resp = client.post(f"/admin/domains/{domain_id}/dns-check")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["mx"]["status"] == "verified"
        assert data["spf"]["status"] == "not_configured"
        assert data["dkim"]["status"] == "propagating"
        assert data["dmarc"]["status"] == "verified"


class TestDkimKeyEndpoint:
    def _setup_self_hosted(self, app):
        with app.app_context():
            domain = Domain(
                name="dkimtest.com",
                is_active=True,
                status="complete",
                imap_host="imap.dkimtest.com",
                imap_port=993,
                imap_tls=True,
                smtp_host="smtp.dkimtest.com",
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
            db.session.commit()
        return domain_id

    @patch("app.admin.services.mail_server.get_mail_client_for_domain")
    def test_returns_key_when_exists(self, mock_get_client, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_self_hosted(app)
        mock_client = MagicMock()
        mock_client.get_dkim_key.return_value = {
            "public_key": "MIGfMA0GCS...",
            "selector": "default",
            "txt_record": "v=DKIM1; k=rsa; p=MIGfMA0GCS...",
        }
        mock_get_client.return_value = mock_client
        resp = client.get(f"/admin/domains/{domain_id}/dkim-key")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_key"] is True
        assert data["public_key"] == "MIGfMA0GCS..."
        assert data["selector"] == "default"

    @patch("app.admin.services.mail_server.get_mail_client_for_domain")
    def test_returns_no_key_when_unavailable(self, mock_get_client, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_self_hosted(app)
        mock_get_client.return_value = None
        resp = client.get(f"/admin/domains/{domain_id}/dkim-key")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_key"] is False

    @patch("app.admin.services.mail_server.get_mail_client_for_domain")
    def test_returns_no_key_on_exception(self, mock_get_client, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_self_hosted(app)
        mock_client = MagicMock()
        mock_client.get_dkim_key.side_effect = Exception("connection failed")
        mock_get_client.return_value = mock_client
        resp = client.get(f"/admin/domains/{domain_id}/dkim-key")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_key"] is False


class TestDkimGenerateEndpoint:
    def _setup_self_hosted(self, app):
        with app.app_context():
            domain = Domain(
                name="genkey.com",
                is_active=True,
                status="complete",
                imap_host="imap.genkey.com",
                imap_port=993,
                imap_tls=True,
                smtp_host="smtp.genkey.com",
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
            db.session.commit()
        return domain_id

    @patch("app.admin.services.mail_server.get_mail_client_for_domain")
    def test_generates_key(self, mock_get_client, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_self_hosted(app)
        mock_client = MagicMock()
        mock_client.generate_dkim_key.return_value = {
            "public_key": "NEWKEY123",
            "selector": "default",
            "txt_record": "v=DKIM1; k=rsa; p=NEWKEY123",
        }
        mock_get_client.return_value = mock_client
        resp = client.post(f"/admin/domains/{domain_id}/dkim-generate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["public_key"] == "NEWKEY123"
        assert data["selector"] == "default"
        mock_client.generate_dkim_key.assert_called_once_with("genkey.com", selector="default")

    def test_rejects_non_self_hosted(self, app, client, _clean_db):
        _setup_admin(client, app)
        with app.app_context():
            domain = Domain(
                name="notself2.com",
                is_active=True,
                status="complete",
                imap_host="imap.notself2.com",
                imap_port=993,
                imap_tls=True,
                smtp_host="smtp.notself2.com",
                smtp_port=587,
                smtp_tls_mode="starttls",
                mail_api_url="http://mail-api:8800",
                mail_api_key="test-key",
            )
            db.session.add(domain)
            db.session.flush()
            domain_id = domain.id
            db.session.commit()
        resp = client.post(f"/admin/domains/{domain_id}/dkim-generate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "Self-hosted" in data["error"]

    @patch("app.admin.services.mail_server.get_mail_client_for_domain")
    def test_rejects_no_mail_api(self, mock_get_client, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_self_hosted(app)
        mock_get_client.return_value = None
        resp = client.post(f"/admin/domains/{domain_id}/dkim-generate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "Mail API" in data["error"]

    @patch("app.admin.services.mail_server.get_mail_client_for_domain")
    def test_handles_generate_exception(self, mock_get_client, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_self_hosted(app)
        mock_client = MagicMock()
        mock_client.generate_dkim_key.side_effect = Exception("API error")
        mock_get_client.return_value = mock_client
        resp = client.post(f"/admin/domains/{domain_id}/dkim-generate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "API error" in data["error"]


class TestSavePlatformServiceConfig:
    def test_save_service_config_creates_row(self, app, client, _clean_db):
        _setup_admin(client, app)
        resp = client.post("/admin/platform-dns/save", data={
            "mx_hostnames": "mx.example.com",
            "mx_priorities": "10",
            "imap_host": "mail.example.com",
            "imap_port": "993",
            "smtp_host": "mail.example.com",
            "smtp_port": "587",
            "smtp_tls_mode": "starttls",
            "carddav_host": "radicale.example.com",
            "carddav_port": "5232",
            "caldav_host": "radicale.example.com",
            "caldav_port": "5232",
        }, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            svc = PlatformServiceConfig.query.first()
            assert svc is not None
            assert svc.imap_host == "mail.example.com"
            assert svc.imap_port == 993
            assert svc.smtp_host == "mail.example.com"
            assert svc.smtp_port == 587
            assert svc.carddav_host == "radicale.example.com"
            assert svc.caldav_host == "radicale.example.com"

    def test_save_service_config_updates_existing(self, app, client, _clean_db):
        _setup_admin(client, app)
        with app.app_context():
            svc = PlatformServiceConfig(imap_host="old.example.com", smtp_host="old.example.com")
            db.session.add(svc)
            db.session.commit()
        client.post("/admin/platform-dns/save", data={
            "mx_hostnames": "",
            "imap_host": "new.example.com",
            "imap_port": "993",
            "smtp_host": "new.example.com",
            "smtp_port": "587",
            "smtp_tls_mode": "starttls",
        }, follow_redirects=True)
        with app.app_context():
            svc = PlatformServiceConfig.query.first()
            assert svc.imap_host == "new.example.com"
            assert svc.smtp_host == "new.example.com"

    def test_renders_service_config_values(self, app, client, _clean_db):
        _setup_admin(client, app)
        with app.app_context():
            svc = PlatformServiceConfig(
                imap_host="mail.test.com",
                imap_port=993,
                smtp_host="mail.test.com",
                smtp_port=587,
                carddav_host="radicale.test.com",
                caldav_host="radicale.test.com",
            )
            db.session.add(svc)
            db.session.commit()
        resp = client.get("/admin/platform-dns")
        assert resp.status_code == 200
        assert b"mail.test.com" in resp.data
        assert b"radicale.test.com" in resp.data


class TestDeleteDomain:
    def _setup_domain_with_accounts(self, app):
        from app.shared.models.core import User, CustomerAccount
        with app.app_context():
            domain = Domain(
                name="deleteme.com",
                is_active=True,
                status="complete",
                imap_host="imap.deleteme.com",
                imap_port=993,
                imap_tls=True,
                smtp_host="smtp.deleteme.com",
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

            user = User(email="user@deleteme.com", role="customer", is_active=True)
            user.password_hash = "x"
            db.session.add(user)
            db.session.flush()

            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain_id,
                email_address="user@deleteme.com",
                auth_type="password",
                username="user@deleteme.com",
                is_active=True,
            )
            db.session.add(account)
            db.session.commit()
        return domain_id

    @patch("app.admin.controllers.admin._mail_api_call")
    def test_delete_domain_removes_everything(self, mock_mail_api, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_domain_with_accounts(app)
        resp = client.post(f"/admin/domains/{domain_id}/delete", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            from app.shared.db import db as _db
            from app.shared.models.core import User, CustomerAccount
            assert _db.session.get(Domain, domain_id) is None
            assert DomainDnsConfig.query.filter_by(domain_id=domain_id).first() is None
            assert CustomerAccount.query.filter_by(domain_id=domain_id).first() is None
            assert User.query.filter_by(email="user@deleteme.com").first() is None
        mock_mail_api.assert_called_once()

    @patch("app.admin.controllers.admin._mail_api_call")
    def test_delete_domain_calls_mail_api(self, mock_mail_api, app, client, _clean_db):
        _setup_admin(client, app)
        domain_id = self._setup_domain_with_accounts(app)
        client.post(f"/admin/domains/{domain_id}/delete", follow_redirects=True)
        mock_mail_api.assert_called_once()
        call_args = mock_mail_api.call_args
        assert call_args[0][0] == "remove_domain"

    def test_delete_domain_requires_admin(self, app, client, _clean_db):
        with app.app_context():
            domain = Domain(
                name="notauth.com",
                is_active=True,
                status="complete",
                imap_host="imap.notauth.com",
                imap_port=993,
                imap_tls=True,
                smtp_host="smtp.notauth.com",
                smtp_port=587,
                smtp_tls_mode="starttls",
            )
            db.session.add(domain)
            db.session.flush()
            domain_id = domain.id
            db.session.commit()
        resp = client.post(f"/admin/domains/{domain_id}/delete")
        assert resp.status_code == 302

    def test_delete_nonexistent_domain(self, app, client, _clean_db):
        _setup_admin(client, app)
        resp = client.post("/admin/domains/9999/delete")
        assert resp.status_code == 404
