from __future__ import annotations


import pyotp

from app.shared.db import db
from app.shared.models.core import User
from app.shared import totp as totp_mod


def _make_user(app, email="user@example.com", role="customer"):
    with app.app_context():
        user = User(email=email, role=role, is_active=True)
        db.session.add(user)
        db.session.flush()
        uid = user.id
        db.session.commit()
        return uid


def test_generate_secret(app):
    _make_user(app)
    with app.app_context():
        secret = totp_mod.generate_secret()
        assert isinstance(secret, str)
        assert len(secret) >= 16
        totp = pyotp.TOTP(secret)
        assert totp.now()


def test_verify_code_valid(app):
    _make_user(app)
    with app.app_context():
        secret = totp_mod.generate_secret()
        code = pyotp.TOTP(secret).now()
        assert totp_mod.verify_code(secret, code) is True


def test_verify_code_invalid(app):
    _make_user(app)
    with app.app_context():
        secret = totp_mod.generate_secret()
        assert totp_mod.verify_code(secret, "000000") is False


def test_verify_code_empty(app):
    with app.app_context():
        assert totp_mod.verify_code("SECRET", "") is False
        assert totp_mod.verify_code("", "123456") is False


def test_verify_code_with_spaces(app):
    _make_user(app)
    with app.app_context():
        secret = totp_mod.generate_secret()
        code = pyotp.TOTP(secret).now()
        spaced = f"{code[:3]} {code[3:]}"
        assert totp_mod.verify_code(secret, spaced) is True


def test_build_provisioning_uri(app):
    with app.app_context():
        secret = totp_mod.generate_secret()
        uri = totp_mod.build_provisioning_uri(secret, "test@example.com")
        assert uri.startswith("otpauth://totp/")
        assert "LocoRoomail" in uri
        assert secret in uri


def test_generate_qr_png(app):
    with app.app_context():
        png = totp_mod.generate_qr_png("otpauth://totp/test")
        assert isinstance(png, bytes)
        assert png[:4] == b"\x89PNG"


def test_generate_backup_codes(app):
    codes = totp_mod.generate_backup_codes()
    assert len(codes) == 10
    for code in codes:
        assert len(code) == 8


def test_enable_and_disable_2fa(app):
    uid = _make_user(app)
    with app.app_context():
        user = db.session.get(User, uid)
        secret = totp_mod.generate_secret()
        totp_mod.enable_2fa(user, secret)

        assert user.totp_enabled is True
        assert user.totp_secret == secret
        assert totp_mod.is_2fa_enabled(user) is True
        assert totp_mod.backup_codes_remaining(user) == 10

        totp_mod.disable_2fa(user)
        assert user.totp_enabled is False
        assert user.totp_secret is None
        assert user.backup_codes is None
        assert totp_mod.is_2fa_enabled(user) is False


def test_verify_backup_code_single_use(app):
    uid = _make_user(app)
    with app.app_context():
        user = db.session.get(User, uid)
        secret = totp_mod.generate_secret()
        codes = totp_mod.enable_2fa(user, secret)

        code = codes[0]
        assert totp_mod.verify_backup_code(user, code) is True
        assert totp_mod.backup_codes_remaining(user) == 9
        assert totp_mod.verify_backup_code(user, code) is False
        assert totp_mod.backup_codes_remaining(user) == 9


def test_verify_backup_code_invalid(app):
    uid = _make_user(app)
    with app.app_context():
        user = db.session.get(User, uid)
        secret = totp_mod.generate_secret()
        totp_mod.enable_2fa(user, secret)

        assert totp_mod.verify_backup_code(user, "ZZZZZZZZ") is False
        assert totp_mod.backup_codes_remaining(user) == 10


def test_verify_backup_code_case_insensitive(app):
    uid = _make_user(app)
    with app.app_context():
        user = db.session.get(User, uid)
        secret = totp_mod.generate_secret()
        codes = totp_mod.enable_2fa(user, secret)

        lower = codes[0].lower()
        assert totp_mod.verify_backup_code(user, lower) is True


def test_regenerate_backup_codes(app):
    uid = _make_user(app)
    with app.app_context():
        user = db.session.get(User, uid)
        secret = totp_mod.generate_secret()
        old_codes = totp_mod.enable_2fa(user, secret)
        old_code = old_codes[0]

        new_codes = totp_mod.regenerate_backup_codes(user)
        assert len(new_codes) == 10
        assert totp_mod.backup_codes_remaining(user) == 10

        assert totp_mod.verify_backup_code(user, old_code) is False


def test_trusted_device_issue_and_validate(app):
    uid = _make_user(app)
    with app.app_context():
        token = totp_mod.issue_trusted_device(uid, "Chrome", "127.0.0.1")
        assert isinstance(token, str)

        device = totp_mod.validate_trusted_device(uid, token)
        assert device is not None
        assert device.user_id == uid


def test_trusted_device_invalid_token(app):
    uid = _make_user(app)
    with app.app_context():
        assert totp_mod.validate_trusted_device(uid, "nonexistent") is None
        assert totp_mod.validate_trusted_device(uid, None) is None


def test_trusted_device_revoke(app):
    uid = _make_user(app)
    with app.app_context():
        token = totp_mod.issue_trusted_device(uid, "Chrome", None)
        device = totp_mod.validate_trusted_device(uid, token)
        assert device is not None
        device_id = device.id

        assert totp_mod.revoke_trusted_device(device_id, uid) is True
        assert totp_mod.validate_trusted_device(uid, token) is None


def test_trusted_device_revoke_all(app):
    uid = _make_user(app)
    with app.app_context():
        t1 = totp_mod.issue_trusted_device(uid, "Chrome", None)
        t2 = totp_mod.issue_trusted_device(uid, "Firefox", None)

        count = totp_mod.revoke_all_trusted_devices(uid)
        assert count == 2

        assert totp_mod.validate_trusted_device(uid, t1) is None
        assert totp_mod.validate_trusted_device(uid, t2) is None


def test_trusted_device_different_users(app):
    uid1 = _make_user(app, email="a@example.com")
    uid2 = _make_user(app, email="b@example.com")
    with app.app_context():
        token = totp_mod.issue_trusted_device(uid1, "Chrome", None)
        assert totp_mod.validate_trusted_device(uid1, token) is not None
        assert totp_mod.validate_trusted_device(uid2, token) is None


def test_list_trusted_devices(app):
    uid = _make_user(app)
    with app.app_context():
        totp_mod.issue_trusted_device(uid, "Chrome", None)
        totp_mod.issue_trusted_device(uid, "Firefox", None)

        devices = totp_mod.list_trusted_devices(uid)
        assert len(devices) == 2


def test_disable_2fa_revokes_trusted_devices(app):
    uid = _make_user(app)
    with app.app_context():
        user = db.session.get(User, uid)
        secret = totp_mod.generate_secret()
        totp_mod.enable_2fa(user, secret)
        token = totp_mod.issue_trusted_device(uid, "Chrome", None)
        assert totp_mod.validate_trusted_device(uid, token) is not None

        totp_mod.disable_2fa(user)
        assert totp_mod.validate_trusted_device(uid, token) is None


def test_describe_user_agent():
    assert "Chrome" in totp_mod.describe_user_agent("Mozilla/5.0 Chrome/100")
    assert "Firefox" in totp_mod.describe_user_agent("Mozilla/5.0 Firefox/100")
    assert "Safari" in totp_mod.describe_user_agent("Mozilla/5.0 Safari/605")
    assert "Edge" in totp_mod.describe_user_agent("Mozilla/5.0 Edg/100")
    assert "Windows" in totp_mod.describe_user_agent("Mozilla/5.0 Windows NT 10.0")
    assert "macOS" in totp_mod.describe_user_agent("Mozilla/5.0 Macintosh")
    assert "Linux" in totp_mod.describe_user_agent("Mozilla/5.0 Linux x86_64")
    assert "Unknown" in totp_mod.describe_user_agent(None)
