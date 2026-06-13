import hashlib
import hmac
import json
import time
import uuid

from flask import current_app


def generate_token(doc_id, user_id, account_id, writable=True):
    secret = _get_secret()
    now = int(time.time())
    payload = {
        "doc_id": doc_id,
        "user_id": user_id,
        "account_id": account_id,
        "writable": writable,
        "iat": now,
        "exp": now + 28800,
        "jti": uuid.uuid4().hex,
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    header = _b64url_encode(payload_bytes)
    sig = _sign(header, secret)
    return f"{header}.{sig}"


def generate_share_token(doc_id, owner_user_id, owner_account_id, share_id, writable):
    secret = _get_secret()
    now = int(time.time())
    payload = {
        "doc_id": doc_id,
        "owner_user_id": owner_user_id,
        "owner_account_id": owner_account_id,
        "share_access": True,
        "share_id": share_id,
        "writable": writable,
        "user_id": 0,
        "account_id": 0,
        "iat": now,
        "exp": now + 28800,
        "jti": uuid.uuid4().hex,
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    header = _b64url_encode(payload_bytes)
    sig = _sign(header, secret)
    return f"{header}.{sig}"


def validate_token(token):
    if not token or "." not in token:
        return None
    parts = token.split(".", 1)
    if len(parts) != 2:
        return None
    header, sig = parts
    secret = _get_secret()
    expected_sig = _sign(header, secret)
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        payload_bytes = _b64url_decode(header)
        payload = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError):
        return None
    now = int(time.time())
    if payload.get("exp", 0) < now:
        return None
    return payload


def _sign(data, secret):
    return hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()


def _get_secret():
    secret = current_app.config.get("WOPI_JWT_SECRET")
    if not secret:
        raise RuntimeError("WOPI_JWT_SECRET is not configured")
    return secret


def _b64url_encode(data):
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data):
    import base64
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)
