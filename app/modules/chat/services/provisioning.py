"""Matrix account provisioning via MAS (Matrix Authentication Service).

Identity mapping (transparent to users):
- A suite account ``test4@test.localhost`` maps to the Matrix user
  ``@test4:<server_name>`` where ``<server_name>`` is the homeserver's own name
  (NOT the email domain). The mapping is deterministic and reversible.

Provisioning model (HLD U25.4/U25.6):
- The homeserver delegates authentication to MAS. Accounts are created through
  the MAS Admin API (OAuth client-credentials, ``urn:mas:admin`` scope) and
  passwords are set via ``set-password``. A pre-existing MAS identity with the
  same localpart is taken over: its password is reset to an app-generated one
  (old sessions of that identity may be revoked — documented behavior).

Credential storage (privacy-first):
- Access token + device id live only in the per-user encrypted chat cache.
- The MAS password backup is stored twice: in the chat cache (encrypted
  with the per-user key) and on CustomerAccount.chat_encrypted_secret
  (encrypted with a key derived from the domain's MAS client secret).
  The domain-key backup survives password changes: if the user's key
  changes, the lazy path logs in again from that backup and self-heals.
"""

from __future__ import annotations

import logging

from app.modules.chat.services import cache_db
from app.modules.chat.services.cache import get_cache_path
from app.modules.chat.services.mas import MasClient, mas_client_for_domain
from app.modules.chat.services.matrix import (
    MatrixClient,
    MatrixError,
    generate_device_password,
    homeserver_url,
    normalize_localpart,
)
from app.modules.mail.services.crypto import derive_key
from app.modules.mail.services.secrets import decrypt_with_key, encrypt_with_key
from app.shared.db import db
from app.shared.keys import get_user_key
from app.shared.models.core import Domain

logger = logging.getLogger(__name__)

_TOKEN_ERROR_CODES = {"M_UNKNOWN_TOKEN", "M_MISSING_TOKEN"}


def _backup_key_hex(domain, account) -> str:
    """Key for the account-row backup, independent of the user's password."""
    return derive_key(domain.matrix_mas_client_secret or "", account.email_address)


def _row_backup(password: str, domain, account) -> bytes:
    return encrypt_with_key(password, _backup_key_hex(domain, account))


def _password_from_row_backup(account, domain) -> str | None:
    if not account.chat_encrypted_secret:
        return None
    try:
        return decrypt_with_key(account.chat_encrypted_secret, _backup_key_hex(domain, account))
    except Exception:
        logger.info(
            "chat row backup not decryptable (pre-password-change copy?) account_id=%s",
            account.id,
        )
        return None


def ensure_matrix_user(account, domain, server_name: str | None = None) -> dict:
    """Ensure the MAS identity for a suite account exists with an app password.

    Creates the user when missing; otherwise takes over the pre-existing
    identity by resetting its password. The password backup is (re)stored on
    the account row encrypted with a key derived from the domain's MAS client
    secret. Returns ``{"created": bool, "matrix_user_id": str | None}`` — the
    Matrix user id is only included when ``server_name`` is supplied (the
    caller knows it from its own logged-in identity on the same homeserver).
    """
    localpart = normalize_localpart(account.email_address, account.username)
    mas = mas_client_for_domain(domain)

    existing = mas.get_user_by_username(localpart)
    created = existing is None
    if existing is not None:
        mas_user_id = existing["id"]
    else:
        try:
            resource = mas.create_user(
                localpart, displayname=account.email_address.split("@", 1)[0]
            )
            mas_user_id = resource["id"]
        except MatrixError as exc:
            if exc.code != "MAS_USER_EXISTS":
                raise
            # Created concurrently (or create reported conflict): resolve it.
            concurrent = mas.get_user_by_username(localpart)
            if concurrent is None:
                raise
            mas_user_id = concurrent["id"]
            created = False

    password = generate_device_password()
    mas.set_password(mas_user_id, password)
    account.chat_encrypted_secret = _row_backup(password, domain, account)
    db.session.commit()
    logger.info(
        "chat identity ensured account_id=%s localpart=%s created=%s",
        account.id,
        localpart,
        created,
    )
    matrix_user_id = f"@{localpart}:{server_name}" if server_name else None
    return {"matrix_user_id": matrix_user_id, "created": created}


def best_effort_provision(account) -> dict | None:
    """Provision the chat identity for a freshly created suite account.

    Non-fatal by design: account creation must never fail because the chat
    server is down or unconfigured. The lazy on-first-open path remains as a
    fallback for anything this misses.
    """
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.matrix_host or not domain.matrix_mas_url:
        logger.debug("chat provisioning skipped (matrix not configured) account_id=%s", account.id)
        return None
    try:
        result = ensure_matrix_user(account, domain)
        logger.info(
            "chat identity provisioned account_id=%s created=%s",
            account.id,
            result["created"],
        )
        return result
    except Exception:
        logger.warning("chat provisioning failed account_id=%s", account.id, exc_info=True)
        return None


def _client_for(account, domain, key):
    base_url = homeserver_url(domain)
    path = get_cache_path(account)
    conn = cache_db.open_cache(path, key)
    try:
        creds = cache_db.get_credentials(conn)
        if creds:
            client = MatrixClient(base_url, creds["access_token"], creds["matrix_user_id"])
            try:
                client.whoami()
                return conn, client, creds
            except MatrixError as exc:
                if exc.code not in _TOKEN_ERROR_CODES:
                    raise
                logger.info(
                    "matrix token invalid for account_id=%s code=%s, attempting re-login",
                    account.id,
                    exc.code,
                )
                conn.close()
                conn = _relogin_from_backup(account, domain, key, path)
                creds = cache_db.get_credentials(conn)
                if not creds:
                    raise
                return (
                    conn,
                    MatrixClient(base_url, creds["access_token"], creds["matrix_user_id"]),
                    creds,
                )
        conn.close()
        return _provision_new(account, domain, key, path, base_url)
    except Exception:
        conn.close()
        raise


def _mas_login(mas: MasClient, localpart: str, password: str) -> dict:
    """Password login through the MAS compat layer; maps auth failures."""
    try:
        return mas.login(localpart, password)
    except MatrixError as exc:
        if getattr(exc, "status", None) in (401, 403):
            raise MatrixError(
                "CHAT_ACCOUNT_RECOVERY_FAILED",
                "The stored chat password was rejected by the authentication service. "
                "Retry to re-provision the chat identity.",
            ) from exc
        raise


def _relogin_from_backup(account, domain, key, path):
    conn = cache_db.open_cache(path, key)
    creds = cache_db.get_credentials(conn)
    password = None
    if creds and creds.get("password_encrypted"):
        try:
            password = decrypt_with_key(creds["password_encrypted"].encode(), key)
        except Exception:
            logger.info("cache password backup not decryptable account_id=%s", account.id)
    if not password:
        password = _password_from_row_backup(account, domain)
    if not password:
        conn.close()
        raise MatrixError(
            "CHAT_ACCOUNT_RECOVERY_FAILED",
            "Stored chat credentials are no longer valid and no password backup exists. "
            "Ask an administrator to delete the old Matrix account, then retry.",
        )
    localpart = normalize_localpart(account.email_address, account.username)
    login = _mas_login(mas_client_for_domain(domain), localpart, password)
    cache_db.set_credentials(
        conn,
        matrix_user_id=login["user_id"],
        access_token=login["access_token"],
        device_id=login.get("device_id"),
        password_encrypted=encrypt_with_key(password, key).decode(),
        homeserver_url=homeserver_url(domain),
    )
    return conn


def _provision_new(account, domain, key, path, base_url):
    conn = cache_db.open_cache(path, key)
    localpart = normalize_localpart(account.email_address, account.username)

    ensure_matrix_user(account, domain)
    # Whether freshly created or pre-existing, we authenticate with the
    # password backup stored on the account row.
    password = _password_from_row_backup(account, domain)
    if not password:
        conn.close()
        raise MatrixError(
            "CHAT_ACCOUNT_RECOVERY_FAILED",
            "The chat identity for this account already exists but no usable credentials "
            "are stored. Ask an administrator to delete the old Matrix account, then retry.",
        )
    login = _mas_login(mas_client_for_domain(domain), localpart, password)
    cache_db.set_credentials(
        conn,
        matrix_user_id=login["user_id"],
        access_token=login["access_token"],
        device_id=login.get("device_id"),
        password_encrypted=encrypt_with_key(password, key).decode(),
        homeserver_url=base_url,
    )
    return (
        conn,
        MatrixClient(base_url, login["access_token"], login["user_id"]),
        {"matrix_user_id": login["user_id"], "access_token": login["access_token"]},
    )


def ensure_chat_client(account, domain, user_id_session):
    """Return (cache_conn, MatrixClient, creds) for the given account.

    The caller owns the returned connection and must close it.
    """
    key = get_user_key(user_id_session)
    if not key:
        raise MatrixError(
            "CHAT_NO_SESSION_KEY", "User session key unavailable; please sign in again"
        )
    return _client_for(account, domain, key)
