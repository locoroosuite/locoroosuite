from __future__ import annotations

import pyotp
from unittest.mock import patch
from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import User, TrustedDevice
from app.shared import totp as totp_mod


def _create_admin(app, email="admin@example.com", password="admin123", enable_2fa=False):
    uid = None
    secret = None
    with app.app_context():
        user = User(
            email=email,
            role="admin",
            is_active=True,
            password_hash=generate_password_hash(password),
        )
        if enable_2fa:
            secret = pyotp.random_base32()
            user.totp_secret = secret
            user.totp_enabled = True
        db.session.add(user)
        db.session.flush()
        uid = user.id
        db.session.commit()
    return uid, secret


class TestAdminLoginWithout2FA:
    @patch("app.admin.controllers.auth.log_audit")
    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_login_no_2fa_redirects_dashboard(self, mock_locked, mock_clear, mock_audit, app, client, _clean_db):
        _create_admin(app)
        resp = client.post("/admin/login", data={"email": "admin@example.com", "password": "admin123"})
        assert resp.status_code == 302
        assert "/admin/" in resp.headers["Location"]

    @patch("app.admin.controllers.auth.log_audit")
    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_login_wrong_password_same_error(self, mock_locked, mock_clear, mock_audit, app, client, _clean_db):
        _create_admin(app, enable_2fa=True)
        resp = client.post("/admin/login", data={"email": "admin@example.com", "password": "wrong"})
        assert resp.status_code == 200
        assert "Invalid credentials" in resp.data.decode()


class TestAdminLoginWith2FA:
    @patch("app.admin.controllers.auth.log_audit")
    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_valid_password_renders_totp_page(self, mock_locked, mock_clear, mock_audit, app, client, _clean_db):
        _create_admin(app, enable_2fa=True)
        resp = client.post("/admin/login", data={"email": "admin@example.com", "password": "admin123"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "verification code" in html.lower() or "two-factor" in html.lower()

    @patch("app.admin.controllers.auth.log_audit")
    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_role_not_set_during_pending_2fa(self, mock_locked, mock_clear, mock_audit, app, client, _clean_db):
        _create_admin(app, enable_2fa=True)
        client.post("/admin/login", data={"email": "admin@example.com", "password": "admin123"})
        with client.session_transaction() as sess:
            assert "role" not in sess
            assert sess.get("_pending_2fa_user_id") is not None

    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_totp_verify_valid_code(self, mock_locked, mock_clear, app, client, _clean_db):
        uid, secret = _create_admin(app, enable_2fa=True)
        with client.session_transaction() as sess:
            sess["_pending_2fa_user_id"] = uid
            sess["_pending_2fa_role"] = "admin"
        code = pyotp.TOTP(secret).now()
        resp = client.post("/admin/twofa", data={"code": code})
        assert resp.status_code == 302
        assert "/admin/" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("role") == "admin"
            assert "_pending_2fa_user_id" not in sess

    @patch("app.admin.controllers.auth.record_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_totp_verify_invalid_code(self, mock_locked, mock_record, app, client, _clean_db):
        uid, secret = _create_admin(app, enable_2fa=True)
        with client.session_transaction() as sess:
            sess["_pending_2fa_user_id"] = uid
            sess["_pending_2fa_role"] = "admin"
        resp = client.post("/admin/twofa", data={"code": "000000"})
        assert resp.status_code == 200
        assert "Invalid code" in resp.data.decode()
        with client.session_transaction() as sess:
            assert "_pending_2fa_user_id" in sess

    def test_totp_verify_no_pending_redirects_login(self, app, client, _clean_db):
        _create_admin(app)
        resp = client.get("/admin/twofa")
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]

    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_backup_code_login(self, mock_locked, mock_clear, app, client, _clean_db):
        uid, secret = _create_admin(app, enable_2fa=True)
        with app.app_context():
            user = db.session.get(User, uid)
            codes = totp_mod.enable_2fa(user, user.totp_secret)
        with client.session_transaction() as sess:
            sess["_pending_2fa_user_id"] = uid
            sess["_pending_2fa_role"] = "admin"
        resp = client.post("/admin/twofa", data={"code": codes[0], "backup_mode": "1"})
        assert resp.status_code == 302
        assert "/admin/" in resp.headers["Location"]

    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_remember_device_sets_cookie(self, mock_locked, mock_clear, app, client, _clean_db):
        uid, secret = _create_admin(app, enable_2fa=True)
        with client.session_transaction() as sess:
            sess["_pending_2fa_user_id"] = uid
            sess["_pending_2fa_role"] = "admin"
        code = pyotp.TOTP(secret).now()
        resp = client.post("/admin/twofa", data={"code": code, "remember_device": "1"})
        assert resp.status_code == 302
        assert totp_mod.TRUSTED_DEVICE_COOKIE in resp.headers.get("Set-Cookie", "")

    @patch("app.admin.controllers.auth.log_audit")
    @patch("app.admin.controllers.auth.clear_failed_login")
    @patch("app.admin.controllers.auth.is_locked", return_value=False)
    def test_trusted_device_skips_2fa(self, mock_locked, mock_clear, mock_audit, app, client, _clean_db):
        uid, secret = _create_admin(app, enable_2fa=True)
        with app.app_context():
            token = totp_mod.issue_trusted_device(uid, "Chrome", "127.0.0.1")
        client.set_cookie(totp_mod.TRUSTED_DEVICE_COOKIE, token)
        resp = client.post("/admin/login", data={"email": "admin@example.com", "password": "admin123"})
        assert resp.status_code == 302
        assert "/admin/" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("role") == "admin"


class TestAdmin2FASettings:
    def test_settings_page_no_2fa(self, admin_client, app):
        client, _ = admin_client
        resp = client.get("/admin/settings/security")
        assert resp.status_code == 200
        assert "Enable 2FA" in resp.data.decode()

    def test_enable_creates_pending_secret(self, admin_client, app):
        client, uid = admin_client
        resp = client.post("/admin/settings/security/enable")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("_pending_totp_secret") is not None

    def test_confirm_page_shows_qr(self, admin_client, app):
        client, uid = admin_client
        with client.session_transaction() as sess:
            sess["_pending_totp_secret"] = pyotp.random_base32()
        resp = client.get("/admin/settings/security/confirm")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "qr" in html.lower() or "qr" in resp.request.path

    def test_confirm_valid_code_enables_2fa(self, admin_client, app):
        client, uid = admin_client
        secret = pyotp.random_base32()
        with client.session_transaction() as sess:
            sess["_pending_totp_secret"] = secret
        code = pyotp.TOTP(secret).now()
        resp = client.post("/admin/settings/security/confirm", data={"code": code})
        assert resp.status_code == 302
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_enabled is True

    def test_confirm_invalid_code_shows_error(self, admin_client, app):
        client, uid = admin_client
        with client.session_transaction() as sess:
            sess["_pending_totp_secret"] = pyotp.random_base32()
        resp = client.post("/admin/settings/security/confirm", data={"code": "000000"})
        assert resp.status_code == 200
        assert "Invalid code" in resp.data.decode()

    def test_disable_with_valid_code(self, admin_client, app):
        client, uid = admin_client
        with app.app_context():
            user = db.session.get(User, uid)
            secret = pyotp.random_base32()
            totp_mod.enable_2fa(user, secret)
        code = pyotp.TOTP(secret).now()
        resp = client.post("/admin/settings/security/disable", data={"code": code})
        assert resp.status_code == 302
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_enabled is False

    def test_disable_with_invalid_code_shows_error(self, admin_client, app):
        client, uid = admin_client
        with app.app_context():
            user = db.session.get(User, uid)
            totp_mod.enable_2fa(user, pyotp.random_base32())
        resp = client.post("/admin/settings/security/disable", data={"code": "000000"})
        assert resp.status_code == 200
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_enabled is True

    def test_revoke_device(self, admin_client, app):
        client, uid = admin_client
        with app.app_context():
            device_id_db = totp_mod.issue_trusted_device(uid, "Chrome", None)
            device = TrustedDevice.query.filter_by(user_id=uid).first()
            did = device.id
        resp = client.post(f"/admin/settings/security/devices/{did}/revoke")
        assert resp.status_code == 302
        with app.app_context():
            device = db.session.get(TrustedDevice, did)
            assert device.revoked_at is not None

    def test_revoke_all_devices(self, admin_client, app):
        client, uid = admin_client
        with app.app_context():
            totp_mod.issue_trusted_device(uid, "Chrome", None)
            totp_mod.issue_trusted_device(uid, "Firefox", None)
        resp = client.post("/admin/settings/security/devices/revoke-all")
        assert resp.status_code == 302
        with app.app_context():
            active = TrustedDevice.query.filter_by(user_id=uid, revoked_at=None).count()
            assert active == 0
