"""Server-held key wrapping for headless push workers (U24.29).

Lets notification workers decrypt per-user caches and mail credentials
without a login session, so push survives server restarts. The privacy
tradeoff is deliberate and documented in the HLD: while push is enabled
for a user, the server can autonomously access their data — the same
trust it already has while they are logged in.

Secret resolution order:
1. ``PUSH_KEYWRAP_SECRET`` env (urlsafe-base64 32 bytes; anything else is
   a fail-early configuration error, never a silent no-op).
2. Key file at ``PUSH_KEYWRAP_KEY_PATH`` env / app config, default
   ``data/push_keywrap.key`` — auto-generated with 0600 permissions.
"""

import base64
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.shared.db import db
from app.shared.keys import get_user_key, set_user_key
from app.shared.models.core import PushKeyStore, PushSubscription

_logger = logging.getLogger(__name__)

_fernet: Fernet | None = None

ENV_SECRET = "PUSH_KEYWRAP_SECRET"
ENV_KEY_PATH = "PUSH_KEYWRAP_KEY_PATH"


class PushKeyConfigError(RuntimeError):
    """Raised when the push keywrap secret is misconfigured (fail early)."""


def _keywrap_path() -> Path:
    configured = os.environ.get(ENV_KEY_PATH)
    if not configured:
        from flask import current_app

        try:
            configured = current_app.config.get("PUSH_KEYWRAP_KEY_PATH")
        except RuntimeError:  # outside app context (e.g. worker bootstrap)
            configured = None
    if configured:
        return Path(configured)
    from app.config import DATA_DIR

    return DATA_DIR / "push_keywrap.key"


def _load_or_create_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    env_secret = os.environ.get(ENV_SECRET)
    if env_secret:
        try:
            key = base64.urlsafe_b64decode(env_secret.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise PushKeyConfigError(
                f"{ENV_SECRET} must be a urlsafe-base64-encoded 32-byte key."
            ) from exc
        if len(key) != 32:
            raise PushKeyConfigError(
                f"{ENV_SECRET} must decode to exactly 32 bytes, got {len(key)}."
            )
        _fernet = Fernet(base64.urlsafe_b64encode(key))
        return _fernet
    path = _keywrap_path()
    if path.exists():
        token = path.read_text(encoding="ascii").strip()
        if token:
            _fernet = Fernet(token.encode("ascii"))
            return _fernet
    path.parent.mkdir(parents=True, exist_ok=True)
    token = Fernet.generate_key().decode("ascii")
    path.write_text(token, encoding="ascii")
    os.chmod(path, 0o600)
    _logger.info("push keywrap secret generated path=%s", path)
    _fernet = Fernet(token.encode("ascii"))
    return _fernet


def _reset_fernet_cache() -> None:
    """Test helper: drop the cached Fernet so env/path overrides apply."""
    global _fernet
    _fernet = None


def wrap_dek(dek_hex: str) -> bytes:
    return _load_or_create_fernet().encrypt(dek_hex.encode("utf-8"))


def unwrap_dek(wrapped: bytes) -> str:
    try:
        return _load_or_create_fernet().decrypt(wrapped).decode("utf-8")
    except InvalidToken as exc:
        raise PushKeyConfigError(
            "Push key store cannot be decrypted: the PUSH_KEYWRAP_SECRET secret "
            "does not match the one used when notifications were enabled. "
            "Restore the original secret, or disable and re-enable notifications."
        ) from exc


def has_active_subscription(customer_id: int) -> bool:
    return (
        db.session.query(PushSubscription.id)
        .filter_by(user_id=customer_id, disabled_at=None)
        .first()
        is not None
    )


def has_push_key_store(customer_id: int) -> bool:
    return db.session.get(PushKeyStore, customer_id) is not None


def store_push_dek_if_subscribed(customer_id: int) -> bool:
    """Wrap the in-memory DEK into the push key store (only when subscribed).

    Returns True when a row was written. Called at login and on subscribe so
    the stored copy always reflects the current DEK.
    """
    dek_hex = get_user_key(customer_id)
    if not dek_hex or not has_active_subscription(customer_id):
        return False
    row = db.session.get(PushKeyStore, customer_id)
    if row is None:
        row = PushKeyStore()
        row.customer_id = customer_id
        db.session.add(row)
    row.wrapped_dek = wrap_dek(dek_hex)
    db.session.commit()
    _logger.info("push key store saved customer_id=%s", customer_id)
    return True


def clear_push_dek(customer_id: int) -> bool:
    row = db.session.get(PushKeyStore, customer_id)
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    _logger.info("push key store cleared customer_id=%s", customer_id)
    return True


def ensure_push_key(customer_id: int) -> str | None:
    """Return the user DEK, loading it from the push key store if needed.

    Returns None when no in-memory key exists and no stored copy can be
    unwrapped (e.g. notifications were never enabled for this user).
    """
    dek_hex = get_user_key(customer_id)
    if dek_hex:
        return dek_hex
    row = db.session.get(PushKeyStore, customer_id)
    if row is None:
        return None
    dek_hex = unwrap_dek(row.wrapped_dek)
    set_user_key(customer_id, dek_hex)
    return dek_hex
