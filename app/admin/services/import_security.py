from datetime import datetime, timezone
import secrets

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

from app.modules.mail.services.secrets import decrypt_secret, encrypt_secret


def import_secret_salt(import_request_id, purpose):
    return f"import-request:{import_request_id}:{purpose}"


def encrypt_import_secret(import_request_id, purpose, value):
    return encrypt_secret(
        value,
        current_app.config["SECRET_KEY"],
        import_secret_salt(import_request_id, purpose),
    )


def decrypt_import_secret(import_request_id, purpose, encrypted_value):
    return decrypt_secret(
        encrypted_value,
        current_app.config["SECRET_KEY"],
        import_secret_salt(import_request_id, purpose),
    )


def new_link_key():
    return secrets.token_urlsafe(24)


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="mail-import-link")


def build_import_token(import_request):
    return _serializer().dumps({"id": import_request.id, "key": import_request.link_key})


def parse_import_token(token):
    try:
        data = _serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    return data


def utcnow():
    return datetime.now(timezone.utc)


def is_request_expired(import_request):
    expires_at = import_request.expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= utcnow()
