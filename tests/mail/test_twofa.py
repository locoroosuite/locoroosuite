from __future__ import annotations

import pyotp
from unittest.mock import patch, MagicMock

from app.shared.db import db
from app.shared.models.core import User, Domain, CustomerAccount, TrustedDevice
from app.shared import totp as totp_mod
from app.shared.keys import get_user_key, clear_user_key


def _setup_domain(app):
    with app.app_context():
        domain = Domain(
            name="example.com",
            is_active=True,
            status="active",
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.commit()


def _mock_imap_patches():
    mock_client = MagicMock()
    return [
        patch("app.modules.mail.controllers.auth.connect_imap", return_value=mock_client),
        patch("app.modules.mail.controllers.auth.login_imap"),
        patch("app.modules.mail.controllers.auth.safe_logout"),
        patch("app.modules.mail.controllers.auth.derive_key", return_value="0" * 64),
        patch("app.modules.mail.controllers.auth.encrypt_with_key", return_value=b"x"),
        patch("app.modules.mail.controllers.auth.build_cache_path", return_value="/tmp/test.db"),
    ]


def _enable_2fa_for_customer(app, email="user@example.com"):
    secret = None
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        secret = pyotp.random_base32()
        user.totp_secret = secret
        user.totp_enabled = True
        db.session.commit()
        uid = user.id
    return uid, secret


class TestCustomerLoginWithout2FA:
    def test_login_no_2fa_redirects_inbox(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            resp = client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 302
        assert "/app/mail/folder/" in resp.headers["Location"]


class TestCustomerLoginWith2FA:
    def test_valid_imap_renders_totp_page(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            resp = client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        uid, secret = _enable_2fa_for_customer(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            resp = client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 200
        assert "verification code" in resp.data.decode().lower() or "two-factor" in resp.data.decode().lower()

    def test_role_not_set_during_pending_2fa(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        uid, secret = _enable_2fa_for_customer(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        with client.session_transaction() as sess:
            assert "role" not in sess
            assert sess.get("_pending_2fa_user_id") == uid

    def test_user_key_set_during_pending_2fa(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        uid, secret = _enable_2fa_for_customer(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        assert get_user_key(uid) is not None
        clear_user_key(uid)

    def test_totp_verify_valid_code_completes_login(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        uid, secret = _enable_2fa_for_customer(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        code = pyotp.TOTP(secret).now()
        resp = client.post("/app/twofa", data={"code": code})
        assert resp.status_code == 302
        assert "/app/mail/folder/" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("role") == "customer"
            assert "_pending_2fa_user_id" not in sess
        clear_user_key(uid)

    def test_totp_verify_invalid_code(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        uid, secret = _enable_2fa_for_customer(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        resp = client.post("/app/twofa", data={"code": "000000"})
        assert resp.status_code == 200
        assert "Invalid code" in resp.data.decode()
        with client.session_transaction() as sess:
            assert "_pending_2fa_user_id" in sess
        clear_user_key(uid)

    def test_totp_verify_no_pending_redirects_login(self, client, app):
        _setup_domain(app)
        resp = client.get("/app/twofa")
        assert resp.status_code == 302
        assert "/app/login" in resp.headers["Location"]

    def test_trusted_device_skips_2fa(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        uid, secret = _enable_2fa_for_customer(app)
        with app.app_context():
            token = totp_mod.issue_trusted_device(uid, "Chrome", "127.0.0.1")
        client.set_cookie(totp_mod.TRUSTED_DEVICE_COOKIE, token)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            resp = client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 302
        assert "/app/mail/folder/" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("role") == "customer"
        clear_user_key(uid)

    def test_backup_code_login(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        uid, secret = _enable_2fa_for_customer(app)
        with app.app_context():
            user = db.session.get(User, uid)
            codes = totp_mod.enable_2fa(user, user.totp_secret)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            client.post("/app/login", data={"email": "user@example.com", "password": "secret"})
        finally:
            for p in patches:
                p.stop()
        resp = client.post("/app/twofa", data={"code": codes[0], "backup_mode": "1"})
        assert resp.status_code == 302
        clear_user_key(uid)


class TestCustomer2FASettings:
    def test_settings_page_no_2fa(self, authed_client):
        client, uid, account_id = authed_client
        resp = client.get("/app/mail/settings/security")
        assert resp.status_code == 200
        assert "Enable 2FA" in resp.data.decode()

    def test_enable_creates_pending_secret(self, authed_client):
        client, uid, account_id = authed_client
        resp = client.post("/app/mail/settings/security/enable")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("_pending_totp_secret") is not None

    def test_confirm_valid_code_enables_2fa(self, authed_client, app):
        client, uid, account_id = authed_client
        secret = pyotp.random_base32()
        with client.session_transaction() as sess:
            sess["_pending_totp_secret"] = secret
        code = pyotp.TOTP(secret).now()
        resp = client.post("/app/mail/settings/security/confirm", data={"code": code})
        assert resp.status_code == 302
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_enabled is True

    def test_confirm_invalid_code(self, authed_client):
        client, uid, account_id = authed_client
        with client.session_transaction() as sess:
            sess["_pending_totp_secret"] = pyotp.random_base32()
        resp = client.post("/app/mail/settings/security/confirm", data={"code": "000000"})
        assert resp.status_code == 200
        assert "Invalid code" in resp.data.decode()

    def test_disable_with_valid_code(self, authed_client, app):
        client, uid, account_id = authed_client
        with app.app_context():
            user = db.session.get(User, uid)
            secret = pyotp.random_base32()
            totp_mod.enable_2fa(user, secret)
        code = pyotp.TOTP(secret).now()
        resp = client.post("/app/mail/settings/security/disable", data={"code": code})
        assert resp.status_code == 302
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_enabled is False

    def test_revoke_device(self, authed_client, app):
        client, uid, account_id = authed_client
        with app.app_context():
            totp_mod.issue_trusted_device(uid, "Chrome", None)
            device = TrustedDevice.query.filter_by(user_id=uid).first()
            did = device.id
        resp = client.post(f"/app/mail/settings/security/devices/{did}/revoke")
        assert resp.status_code == 302
        with app.app_context():
            device = db.session.get(TrustedDevice, did)
            assert device.revoked_at is not None

    def test_revoke_all_devices(self, authed_client, app):
        client, uid, account_id = authed_client
        with app.app_context():
            totp_mod.issue_trusted_device(uid, "Chrome", None)
            totp_mod.issue_trusted_device(uid, "Firefox", None)
        resp = client.post("/app/mail/settings/security/devices/revoke-all")
        assert resp.status_code == 302
        with app.app_context():
            active = TrustedDevice.query.filter_by(user_id=uid, revoked_at=None).count()
            assert active == 0
