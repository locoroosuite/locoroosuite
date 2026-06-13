from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import User, Domain, CustomerAccount


def _create_admin_and_domain(app):
    with app.app_context():
        admin = User(role="admin", email="admin@test.local", password_hash=generate_password_hash("admin123"))
        db.session.add(admin)
        domain = Domain(
            name="test.local",
            imap_host="dovecot",
            imap_port=143,
            smtp_host="postfix",
            smtp_port=587,
            smtp_tls_mode="starttls",
            status="complete",
        )
        db.session.add(domain)
        db.session.commit()
        return admin.id, domain.id


def _login_admin(client, user_id):
    with client.session_transaction() as sess:
        sess["role"] = "admin"
        sess["user_id"] = user_id


class TestCreateCustomerWithPassword:
    def test_creates_customer_with_password(self, app, client, _clean_db):
        admin_id, domain_id = _create_admin_and_domain(app)
        _login_admin(client, admin_id)

        mock_client = MagicMock()
        with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=mock_client):
            resp = client.post("/admin/customers/new", data={
                "username": "user",
                "domain_id": domain_id,
                "password": "secretpass",
                "create_mode": "password",
            }, follow_redirects=False)

        assert resp.status_code == 302

        with app.app_context():
            user = User.query.filter_by(email="user@test.local").first()
            assert user is not None
            assert user.role == "customer"

            account = CustomerAccount.query.filter_by(customer_id=user.id).first()
            assert account is not None
            assert account.encrypted_secret is None
            assert account.signup_token is None
            mock_client.add_user.assert_called_once()

    def test_duplicate_email_rejected(self, app, client, _clean_db):
        admin_id, domain_id = _create_admin_and_domain(app)
        _login_admin(client, admin_id)

        with patch("app.admin.services.mail_server.get_mail_client_for_domain", return_value=MagicMock()):
            client.post("/admin/customers/new", data={
                "username": "user",
                "domain_id": domain_id,
                "password": "secretpass",
                "create_mode": "password",
            })

        resp = client.post("/admin/customers/new", data={
            "username": "user",
            "domain_id": domain_id,
            "password": "otherpass",
            "create_mode": "password",
        })
        assert resp.status_code == 302


class TestCreateCustomerWithInvite:
    def test_creates_invite_link(self, app, client, _clean_db):
        admin_id, domain_id = _create_admin_and_domain(app)
        _login_admin(client, admin_id)

        resp = client.post("/admin/customers/new", data={
            "username": "user",
            "domain_id": domain_id,
            "create_mode": "invite",
        })

        assert resp.status_code == 200
        assert b"invitation link" in resp.data.lower() or b"Invitation link" in resp.data

        with app.app_context():
            user = User.query.filter_by(email="user@test.local").first()
            assert user is not None

            account = CustomerAccount.query.filter_by(customer_id=user.id).first()
            assert account is not None
            assert account.signup_token is not None
            assert account.signup_expires_at is not None

    def test_no_domain_shows_error(self, app, client, _clean_db):
        admin_id, _ = _create_admin_and_domain(app)
        _login_admin(client, admin_id)

        resp = client.post("/admin/customers/new", data={
            "username": "user",
            "create_mode": "invite",
        }, follow_redirects=True)

        assert resp.status_code == 200
        assert b"domain" in resp.data.lower()


class TestSignupPage:
    def test_shows_signup_form(self, app, client, _clean_db):
        with app.app_context():
            user = User(role="customer", email="user@test.local")
            db.session.add(user)
            db.session.flush()
            domain = Domain(name="test.local", imap_host="d", imap_port=143, smtp_host="p", smtp_port=587, smtp_tls_mode="starttls", status="complete")
            db.session.add(domain)
            db.session.flush()
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="user@test.local",
                auth_type="password",
                username="user@test.local",
                signup_token="test-token-123",
                signup_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(account)
            db.session.commit()

        resp = client.get("/signup/test-token-123")
        assert resp.status_code == 200
        assert b"user@test.local" in resp.data
        assert b"Set up your account" in resp.data

    def test_invalid_token_returns_404(self, app, client, _clean_db):
        resp = client.get("/signup/nonexistent-token")
        assert resp.status_code == 404
        assert b"invalid" in resp.data.lower() or b"expired" in resp.data.lower()

    def test_expired_token_returns_410(self, app, client, _clean_db):
        with app.app_context():
            user = User(role="customer", email="user@test.local")
            db.session.add(user)
            db.session.flush()
            domain = Domain(name="test.local", imap_host="d", imap_port=143, smtp_host="p", smtp_port=587, smtp_tls_mode="starttls", status="complete")
            db.session.add(domain)
            db.session.flush()
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="user@test.local",
                auth_type="password",
                username="user@test.local",
                signup_token="expired-token",
                signup_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            db.session.add(account)
            db.session.commit()

        resp = client.get("/signup/expired-token")
        assert resp.status_code == 410

    def test_already_completed_redirects_to_login(self, app, client, _clean_db):
        with app.app_context():
            user = User(role="customer", email="user@test.local")
            db.session.add(user)
            db.session.flush()
            domain = Domain(name="test.local", imap_host="d", imap_port=143, smtp_host="p", smtp_port=587, smtp_tls_mode="starttls", status="complete")
            db.session.add(domain)
            db.session.flush()
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="user@test.local",
                auth_type="password",
                username="user@test.local",
                signup_token="used-token",
                signup_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                encrypted_secret=b"already-set",
            )
            db.session.add(account)
            db.session.commit()

        resp = client.get("/signup/used-token", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestSignupSubmit:
    def test_signup_form_contains_correct_action_url(self, app, client, _clean_db):
        with app.app_context():
            user = User(role="customer", email="user@test.local")
            db.session.add(user)
            db.session.flush()
            domain = Domain(name="test.local", imap_host="d", imap_port=143, smtp_host="p", smtp_port=587, smtp_tls_mode="starttls", status="complete")
            db.session.add(domain)
            db.session.flush()
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="user@test.local",
                auth_type="password",
                username="user@test.local",
                signup_token="action-check-token",
                signup_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(account)
            db.session.commit()

        resp = client.get("/signup/action-check-token")
        assert resp.status_code == 200
        assert b'action="/signup/action-check-token"' in resp.data

    @patch("app.admin.controllers.admin._mail_api_call")
    def test_completes_signup(self, mock_mail, app, client, _clean_db):
        with app.app_context():
            user = User(role="customer", email="user@test.local")
            db.session.add(user)
            db.session.flush()
            domain = Domain(name="test.local", imap_host="d", imap_port=143, smtp_host="p", smtp_port=587, smtp_tls_mode="starttls", status="complete")
            db.session.add(domain)
            db.session.flush()
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="user@test.local",
                auth_type="password",
                username="user@test.local",
                signup_token="valid-token",
                signup_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(account)
            db.session.commit()

        resp = client.post("/signup/valid-token", data={
            "password": "mypassword",
            "password_confirm": "mypassword",
        }, follow_redirects=False)

        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

        with app.app_context():
            account = CustomerAccount.query.filter_by(signup_token=None, email_address="user@test.local").first()
            assert account is not None
            assert account.signup_token is None
            assert account.signup_expires_at is None

    def test_password_mismatch(self, app, client, _clean_db):
        with app.app_context():
            user = User(role="customer", email="user@test.local")
            db.session.add(user)
            db.session.flush()
            domain = Domain(name="test.local", imap_host="d", imap_port=143, smtp_host="p", smtp_port=587, smtp_tls_mode="starttls", status="complete")
            db.session.add(domain)
            db.session.flush()
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="user@test.local",
                auth_type="password",
                username="user@test.local",
                signup_token="mismatch-token",
                signup_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(account)
            db.session.commit()

        resp = client.post("/signup/mismatch-token", data={
            "password": "mypassword",
            "password_confirm": "different",
        })
        assert resp.status_code == 200
        assert b"Passwords do not match" in resp.data

    def test_empty_password(self, app, client, _clean_db):
        with app.app_context():
            user = User(role="customer", email="user@test.local")
            db.session.add(user)
            db.session.flush()
            domain = Domain(name="test.local", imap_host="d", imap_port=143, smtp_host="p", smtp_port=587, smtp_tls_mode="starttls", status="complete")
            db.session.add(domain)
            db.session.flush()
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="user@test.local",
                auth_type="password",
                username="user@test.local",
                signup_token="empty-token",
                signup_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(account)
            db.session.commit()

        resp = client.post("/signup/empty-token", data={
            "password": "",
            "password_confirm": "",
        })
        assert resp.status_code == 200
        assert b"Password is required" in resp.data
