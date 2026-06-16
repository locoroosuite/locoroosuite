from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import secrets

from datetime import datetime, timedelta, timezone

import pyotp

from app.shared.db import db
from app.shared.models.core import TrustedDevice, User

logger = logging.getLogger(__name__)

TRUSTED_DEVICE_COOKIE = "lr_trusted_device"
TRUSTED_DEVICE_DAYS = 30
BACKUP_CODE_COUNT = 10


def generate_secret() -> str:
    return pyotp.random_base32()


def build_provisioning_uri(secret: str, email: str, issuer: str = "LocoRoomail") -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def generate_qr_png(data_uri: str) -> bytes:
    import qrcode

    buf = io.BytesIO()
    img = qrcode.make(data_uri)
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_backup_codes() -> list[str]:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return ["".join(secrets.choice(alphabet) for _ in range(8)) for _ in range(BACKUP_CODE_COUNT)]


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.upper().strip().encode()).hexdigest()


def store_backup_codes(user: User, codes: list[str]) -> list[str]:
    hashed = [_hash_code(c) for c in codes]
    user.backup_codes = json.dumps(hashed)
    db.session.commit()
    return codes


def verify_backup_code(user: User, code: str) -> bool:
    if not user.backup_codes or not code:
        return False
    try:
        stored: list[str] = json.loads(user.backup_codes)
    except (TypeError, ValueError):
        return False
    target = _hash_code(code)
    if target not in stored:
        return False
    stored.remove(target)
    user.backup_codes = json.dumps(stored) if stored else None
    db.session.commit()
    return True


def backup_codes_remaining(user: User) -> int:
    if not user.backup_codes:
        return 0
    try:
        return len(json.loads(user.backup_codes))
    except (TypeError, ValueError):
        return 0


def enable_2fa(user: User, secret: str) -> list[str]:
    user.totp_secret = secret
    user.totp_enabled = True
    codes = generate_backup_codes()
    store_backup_codes(user, codes)
    return codes


def disable_2fa(user: User) -> None:
    user.totp_secret = None
    user.totp_enabled = False
    user.backup_codes = None
    revoke_all_trusted_devices(user.id)
    db.session.commit()


def regenerate_backup_codes(user: User) -> list[str]:
    codes = generate_backup_codes()
    store_backup_codes(user, codes)
    return codes


# ---- Trusted device helpers ----


def generate_device_token() -> str:
    raw = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_trusted_device(user_id: int, user_agent: str | None, ip_address: str | None) -> str:
    token = generate_device_token()
    now = datetime.now(timezone.utc)
    device = TrustedDevice(
        user_id=user_id,
        token_hash=_hash_token(token),
        user_agent=user_agent,
        ip_address=ip_address,
        created_at=now,
        expires_at=now + timedelta(days=TRUSTED_DEVICE_DAYS),
    )
    db.session.add(device)
    db.session.commit()
    return token


def validate_trusted_device(user_id: int, token: str | None) -> TrustedDevice | None:
    if not token:
        return None
    device = TrustedDevice.query.filter_by(
        token_hash=_hash_token(token), user_id=user_id, revoked_at=None,
    ).first()
    if not device:
        return None
    expires = device.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    device.last_used_at = datetime.now(timezone.utc)
    db.session.commit()
    return device


def revoke_trusted_device(device_id: int, user_id: int) -> bool:
    device = TrustedDevice.query.filter_by(id=device_id, user_id=user_id).first()
    if not device:
        return False
    device.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return True


def revoke_all_trusted_devices(user_id: int) -> int:
    count = TrustedDevice.query.filter_by(
        user_id=user_id, revoked_at=None,
    ).update({"revoked_at": datetime.now(timezone.utc)})
    db.session.commit()
    return count


def list_trusted_devices(user_id: int) -> list[TrustedDevice]:
    return (
        TrustedDevice.query
        .filter_by(user_id=user_id, revoked_at=None)
        .order_by(TrustedDevice.created_at.desc())
        .all()
    )


def describe_user_agent(ua: str | None) -> str:
    if not ua:
        return "Unknown device"
    browser = "Browser"
    os_name = ""
    ua_lower = ua.lower()
    if "edg/" in ua_lower:
        browser = "Edge"
    elif "chrome" in ua_lower:
        browser = "Chrome"
    elif "firefox" in ua_lower:
        browser = "Firefox"
    elif "safari" in ua_lower:
        browser = "Safari"
    if "windows" in ua_lower:
        os_name = "Windows"
    elif "mac" in ua_lower or "darwin" in ua_lower:
        os_name = "macOS"
    elif "linux" in ua_lower:
        os_name = "Linux"
    elif "android" in ua_lower:
        os_name = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower:
        os_name = "iOS"
    if os_name:
        return f"{browser} on {os_name}"
    return browser


def is_2fa_enabled(user: User) -> bool:
    return bool(user.totp_enabled and user.totp_secret)
