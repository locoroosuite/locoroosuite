import os
import base64
import hashlib
import json
import logging
import secrets

from cryptography.fernet import Fernet

from app.shared.db import db
from app.shared.models.core import ApiToken, CustomerAccount

_logger = logging.getLogger(__name__)


def generate_dek():
    return os.urandom(32).hex()


def ensure_api_enabled(customer_id: int, credential_key_hex: str) -> bool:
    from app.modules.mail.services.secrets import decrypt_with_key, encrypt_with_key
    from app.shared.keys import set_user_key, get_user_key

    accounts = CustomerAccount.query.filter_by(
        customer_id=customer_id, is_active=True
    ).all()
    if not accounts:
        return False

    needs_dek = any(not a.dek_wrapped_cred for a in accounts)

    if all(a.api_enabled for a in accounts) and not needs_dek:
        existing_dek = get_user_key(customer_id)
        if existing_dek:
            return True
        return True

    dek_hex = get_user_key(customer_id) or generate_dek()
    wrapped_dek = wrap_dek_with_credential(dek_hex, credential_key_hex)

    for acc in accounts:
        if acc.encrypted_secret:
            old_key = credential_key_hex
            if acc.api_enabled and acc.dek_wrapped_cred:
                try:
                    old_key = unwrap_dek_from_credential(acc.dek_wrapped_cred, credential_key_hex)
                except Exception:
                    old_key = credential_key_hex
            secret_plain = decrypt_with_key(acc.encrypted_secret, old_key)
            acc.encrypted_secret = encrypt_with_key(secret_plain, dek_hex)
        acc.api_enabled = True
        acc.dek_wrapped_cred = wrapped_dek
        if acc.cache_db_path:
            from app.modules.mail.services.cache import purge_cache
            purge_cache(acc.cache_db_path)
            acc.cache_db_path = None

    set_user_key(customer_id, dek_hex)
    db.session.commit()

    _logger.info("API auto-enabled for customer %s via OAuth", customer_id)
    return True


def wrap_dek_with_credential(dek_hex, credential_key_hex):
    f = _fernet_from_key(credential_key_hex)
    return f.encrypt(dek_hex.encode())


def unwrap_dek_from_credential(wrapped_dek, credential_key_hex):
    f = _fernet_from_key(credential_key_hex)
    return f.decrypt(wrapped_dek).decode()


def wrap_dek_with_token(dek_hex, raw_token_bytes):
    token_key_hex = hashlib.sha256(raw_token_bytes).hexdigest()
    f = _fernet_from_key(token_key_hex)
    return f.encrypt(dek_hex.encode())


def unwrap_dek_from_token(wrapped_dek, raw_token_bytes):
    token_key_hex = hashlib.sha256(raw_token_bytes).hexdigest()
    f = _fernet_from_key(token_key_hex)
    return f.decrypt(wrapped_dek).decode()


def generate_token():
    raw = secrets.token_bytes(32)
    token_value = "lr_" + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    token_hash = hashlib.sha256(token_value.encode()).hexdigest()
    return token_value, token_hash


def create_api_token(customer_id, dek_hex, name, scopes):
    token_value, token_hash = generate_token()
    raw_token_bytes = token_value.encode()
    wrapped_dek = wrap_dek_with_token(dek_hex, raw_token_bytes)
    token = ApiToken(
        customer_id=customer_id,
        token_hash=token_hash,
        name=name,
        scopes=json.dumps(scopes),
        wrapped_dek=wrapped_dek,
    )
    db.session.add(token)
    db.session.commit()
    return token_value, token


def revoke_api_token(token_id, customer_id):
    token = ApiToken.query.filter_by(id=token_id, customer_id=customer_id).first()
    if not token:
        return False
    db.session.delete(token)
    db.session.commit()
    return True


def authenticate_token(raw_token_value):
    token_hash = hashlib.sha256(raw_token_value.encode()).hexdigest()
    token = ApiToken.query.filter_by(token_hash=token_hash).first()
    if not token:
        return None, None
    try:
        dek_hex = unwrap_dek_from_token(token.wrapped_dek, raw_token_value.encode())
    except Exception:
        return None, None
    token.last_used_at = db.func.now()
    db.session.commit()
    scopes = json.loads(token.scopes)
    return token, {"dek": dek_hex, "scopes": scopes, "customer_id": token.customer_id}


def _fernet_from_key(key_hex):
    key_bytes = bytes.fromhex(key_hex)
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)
