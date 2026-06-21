from unittest.mock import patch

from app.shared.db import db
from app.shared.models.core import User, Domain


class TestSetupPage:
    def test_setup_shows_form_when_no_admin(self, app, client, _clean_db):
        resp = client.get("/admin/setup")
        assert resp.status_code == 200
        assert b"Welcome to LocoRooSuite" in resp.data
        assert b"test.localhost" in resp.data

    def test_setup_redirects_to_login_when_admin_exists(self, app, client, _clean_db):
        with app.app_context():
            from werkzeug.security import generate_password_hash
            admin = User(role="admin", email="admin@example.com", password_hash=generate_password_hash("x"))
            db.session.add(admin)
            db.session.commit()

        resp = client.get("/admin/setup", follow_redirects=False)
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]

    def test_admin_login_redirects_to_setup_when_no_admin(self, app, client, _clean_db):
        resp = client.get("/admin/login", follow_redirects=False)
        assert resp.status_code == 302
        assert "/admin/setup" in resp.headers["Location"]

    def test_admin_dashboard_redirects_to_setup_when_no_admin(self, app, client, _clean_db):
        resp = client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/admin/setup" in resp.headers["Location"]


class TestSetupSubmit:
    def test_creates_admin_and_domain(self, app, client, _clean_db):
        with patch("app.admin.controllers.auth._sync_setup_domain_to_mail_api"):
            resp = client.post("/admin/setup", data={
                "email": "admin@test.local",
                "password": "secret123",
                "password_confirm": "secret123",
                "domain": "test.localhost",
            }, follow_redirects=False)

        assert resp.status_code == 302
        assert "/admin/" in resp.headers["Location"]

        with app.app_context():
            admin = User.query.filter_by(role="admin").first()
            assert admin is not None
            assert admin.email == "admin@test.local"

            domain = Domain.query.filter_by(name="test.localhost").first()
            assert domain is not None
            assert domain.imap_host == "dovecot"
            assert domain.imap_port == 143
            assert domain.smtp_host == "postfix"
            assert domain.smtp_port == 587
            assert domain.status == "complete"

    def test_auto_logs_in_admin(self, app, client, _clean_db):
        with patch("app.admin.controllers.auth._sync_setup_domain_to_mail_api"):
            client.post("/admin/setup", data={
                "email": "admin@test.local",
                "password": "secret123",
                "password_confirm": "secret123",
                "domain": "test.localhost",
            }, follow_redirects=False)

        with client.session_transaction() as sess:
            assert sess["role"] == "admin"
            assert sess["user_id"] is not None

    def test_password_mismatch(self, app, client, _clean_db):
        resp = client.post("/admin/setup", data={
            "email": "admin@test.local",
            "password": "secret123",
            "password_confirm": "different",
            "domain": "test.localhost",
        })
        assert resp.status_code == 200
        assert b"Passwords do not match" in resp.data

        with app.app_context():
            assert User.query.filter_by(role="admin").first() is None

    def test_missing_email(self, app, client, _clean_db):
        resp = client.post("/admin/setup", data={
            "email": "",
            "password": "secret123",
            "password_confirm": "secret123",
            "domain": "test.localhost",
        })
        assert resp.status_code == 200
        assert b"Email is required" in resp.data

    def test_missing_password(self, app, client, _clean_db):
        resp = client.post("/admin/setup", data={
            "email": "admin@test.local",
            "password": "",
            "password_confirm": "",
            "domain": "test.localhost",
        })
        assert resp.status_code == 200
        assert b"Password is required" in resp.data

    def test_default_domain(self, app, client, _clean_db):
        with patch("app.admin.controllers.auth._sync_setup_domain_to_mail_api"):
            resp = client.post("/admin/setup", data={
                "email": "admin@test.local",
                "password": "secret123",
                "password_confirm": "secret123",
            }, follow_redirects=False)

        assert resp.status_code == 302

        with app.app_context():
            domain = Domain.query.filter_by(name="test.localhost").first()
            assert domain is not None

    def test_post_redirects_to_login_when_admin_exists(self, app, client, _clean_db):
        with app.app_context():
            from werkzeug.security import generate_password_hash
            admin = User(role="admin", email="admin@example.com", password_hash=generate_password_hash("x"))
            db.session.add(admin)
            db.session.commit()

        resp = client.post("/admin/setup", data={
            "email": "new@test.local",
            "password": "secret123",
            "password_confirm": "secret123",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]

    def test_setup_cannot_be_accessed_again_after_creation(self, app, client, _clean_db):
        with patch("app.admin.controllers.auth._sync_setup_domain_to_mail_api"):
            client.post("/admin/setup", data={
                "email": "admin@test.local",
                "password": "secret123",
                "password_confirm": "secret123",
            })

        resp = client.get("/admin/setup", follow_redirects=False)
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]

    def test_setup_sets_just_completed_session_flag(self, app, client, _clean_db):
        with patch("app.admin.controllers.auth._sync_setup_domain_to_mail_api"):
            client.post("/admin/setup", data={
                "email": "admin@test.local",
                "password": "secret123",
                "password_confirm": "secret123",
            })

        with client.session_transaction() as sess:
            assert sess.get("just_completed_setup") is True

    def test_dashboard_shows_setup_banner_after_setup(self, app, client, _clean_db):
        with patch("app.admin.controllers.auth._sync_setup_domain_to_mail_api"):
            client.post("/admin/setup", data={
                "email": "admin@test.local",
                "password": "secret123",
                "password_confirm": "secret123",
            })

        resp = client.get("/admin/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Your environment is ready" in html
        assert "test.localhost" in html
        assert "admin@test.local" in html
        assert "create your first customer" in html

    def test_dashboard_no_banner_on_normal_login(self, app, client, _clean_db):
        with app.app_context():
            from werkzeug.security import generate_password_hash
            admin = User(role="admin", email="admin@example.com", password_hash=generate_password_hash("secret123"))
            db.session.add(admin)
            db.session.commit()

        client.post("/admin/login", data={"email": "admin@example.com", "password": "secret123"})
        resp = client.get("/admin/")
        assert resp.status_code == 200
        assert "Your environment is ready" not in resp.data.decode()

    def test_dismiss_setup_banner_clears_flag(self, app, client, _clean_db):
        with patch("app.admin.controllers.auth._sync_setup_domain_to_mail_api"):
            client.post("/admin/setup", data={
                "email": "admin@test.local",
                "password": "secret123",
                "password_confirm": "secret123",
            })

        resp = client.post("/admin/dismiss-setup-banner")
        assert resp.status_code == 200
        assert resp.json == {"ok": True}

        resp = client.get("/admin/")
        assert "Your environment is ready" not in resp.data.decode()
