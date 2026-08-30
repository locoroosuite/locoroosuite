"""Matrix account provisioning: register at account creation, recover credentials.

Identity mapping (transparent to users):
- A suite account ``test4@test.localhost`` maps to the Matrix user
  ``@test4:<server_name>`` where ``<server_name>`` is the homeserver's own name
  (NOT the email domain). The mapping is deterministic and reversible.

Credential storage (privacy-first):
- Access token + device id live only in the per-user encrypted chat cache.
- The Matrix password backup is stored twice: in the chat cache (encrypted
  with the per-user key) and on CustomerAccount.chat_encrypted_secret
  (encrypted with a key derived from the domain registration shared secret).
  The shared-secret backup survives password changes: if the user's key
  changes, the lazy path logs in again from that backup and self-heals.
"""

from __future__ import annotations

import logging

from app.modules.chat.services import cache_db
from app.modules.chat.services.cache import get_cache_path
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
    return derive_key(domain.matrix_shared_secret or "", account.email_address)


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
    """Ensure the Matrix account for a suite account exists on the homeserver.

    Returns {"matrix_user_id": ..., "created": bool}. Uses shared-secret
    registration; when the user already exists, ``server_name`` (from any
    logged-in identity on the same homeserver) is required to build the id.
    Stores the password backup on the account row when a new user is created.
    """
    localpart = normalize_localpart(account.email_address, account.username)
    password = generate_device_password()
    client = MatrixClient(homeserver_url(domain))
    try:
        reg = client.admin_register(localpart, password, domain.matrix_shared_secret or "")
        created = True
    except MatrixError as exc:
        if exc.code != "M_USER_IN_USE":
            raise
        if not server_name:
            raise MatrixError(
                "CHAT_CANNOT_RESOLVE_USER",
                f"The chat identity for {account.email_address} already exists but its id "
                "cannot be resolved right now. Ask that user to open the Chat page once.",
            ) from exc
        reg = {"user_id": f"@{localpart}:{server_name}"}
        created = False

    if created:
        try:
            client.set_displayname(reg["user_id"], account.email_address.split("@", 1)[0])
        except MatrixError:
            logger.info("could not set displayname for %s (non-fatal)", reg["user_id"])
        account.chat_encrypted_secret = _row_backup(password, domain, account)
        db.session.commit()
    return {"matrix_user_id": reg["user_id"], "created": created}


def best_effort_provision(account) -> dict | None:
    """Provision the Matrix identity for a freshly created suite account.

    Non-fatal by design: account creation must never fail because the chat
    server is down or unconfigured. The lazy on-first-open path remains as a
    fallback for anything this misses.
    """
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.matrix_host:
        logger.debug("chat provisioning skipped (matrix not configured) account_id=%s", account.id)
        return None
    try:
        result = ensure_matrix_user(account, domain)
        logger.info(
            "chat identity provisioned account_id=%s matrix_user_id=%s created=%s",
            account.id,
            result["matrix_user_id"],
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
    client = MatrixClient(homeserver_url(domain))
    login = client.login(localpart, password)
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
    # shared-secret password backup stored on the account row.
    password = _password_from_row_backup(account, domain)
    if not password:
        conn.close()
        raise MatrixError(
            "CHAT_ACCOUNT_RECOVERY_FAILED",
            "The chat identity for this account already exists but no usable credentials "
            "are stored. Ask an administrator to delete the old Matrix account, then retry.",
        )
    login = MatrixClient(base_url).login(localpart, password)
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
