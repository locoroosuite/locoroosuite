"""Shared draft + reply-threading service (U6.4, U6.5a-c, U15.26).

Used by both the REST API controllers and the MCP tools so draft
behaviour cannot drift between surfaces:

- One identifier contract: every write accepts either the cache message
  ``id`` (what read endpoints expose) or the IMAP ``uid``; the server
  resolves cache-id → UID via the Drafts rows in the mail cache. An
  identifier that matches no existing draft raises DraftNotFoundError —
  never a silent no-op (U6.5b).
- Honest mutations: replace verifies the old draft is resolvable before
  appending (no silent duplicates); delete verifies the UID exists and
  reports real deletion (U6.5c).
- Drafts are stored ``\\Draft \\Seen`` — drafts are never unread mail
  (U6.4, issue #9).
- Reply threading: drafts and sends accept In-Reply-To/References so the
  conversation groups correctly in thread views (U4.7, issue #6).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

_logger = logging.getLogger(__name__)

DRAFT_FLAGS = ["\\Draft", "\\Seen"]


class DraftNotFoundError(Exception):
    """Identifier matched no existing draft in the Drafts folder (U6.5b)."""

    def __init__(self, identifier):
        self.identifier = str(identifier)
        super().__init__(f"No draft found for identifier {self.identifier!r}")


class DraftDeletionError(Exception):
    """IMAP reported success but the draft UID is still present (U6.5c)."""


# SQLite INTEGER tops out at a signed 64-bit value; a digit identifier
# beyond it can never match a cache row id.
_SQLITE_MAX_INT = 2**63 - 1


def _is_drafts_folder(folder_name: str) -> bool:
    from app.modules.mail.services.folder_aliases import canonical_folder_key

    return canonical_folder_key(str(folder_name or "")) == "drafts"


def drafts_folder_rows(conn) -> list:
    cursor = conn.execute("SELECT DISTINCT folder FROM messages WHERE folder IS NOT NULL")
    return [r["folder"] for r in cursor.fetchall() if _is_drafts_folder(r["folder"])]


def resolve_draft_identifier(conn, identifier) -> Any:
    """Resolve a draft identifier (cache id or IMAP uid) to its cache row.

    Digit identifiers prefer the cache ``id`` space (the id read endpoints
    expose); both spaces fall back to each other so either is accepted
    (U6.5b). Returns the row or None.
    """
    ident = str(identifier).strip()
    if not ident:
        return None
    folders = drafts_folder_rows(conn)
    if not folders:
        return None
    placeholders = ",".join(["?"] * len(folders))
    # A digit beyond SQLite's INTEGER range can never match a row id;
    # binding it anyway raises OverflowError (misclassified upstream as a
    # mailbox failure). Skip to the uid lookup so hostile identifier
    # shapes resolve to None -> DraftNotFoundError (U6.5b).
    if ident.isdigit() and int(ident) <= _SQLITE_MAX_INT:
        row = conn.execute(
            f"SELECT * FROM messages WHERE id = ? AND folder IN ({placeholders})",
            (int(ident), *folders),
        ).fetchone()
        if row is not None:
            return row
    return conn.execute(
        f"SELECT * FROM messages WHERE uid = ? AND folder IN ({placeholders})",
        (ident, *folders),
    ).fetchone()


def resolve_reply_context(
    conn,
    reply_to_message_id: str | int | None,
    in_reply_to: str | None = None,
) -> tuple[str | None, str | None]:
    """Derive (In-Reply-To, References) for a reply send/draft (U15.26).

    ``reply_to_message_id`` is a cache message id (any folder) — the
    server resolves it to the source message's Message-ID/References.
    A raw ``in_reply_to`` Message-ID is used as-is. Raises ValueError
    when the cache id matches no message (loud, before anything is
    sent or appended).
    """
    if reply_to_message_id is not None and str(reply_to_message_id).strip():
        row = conn.execute(
            "SELECT message_id, in_reply_to, ref_list FROM messages WHERE id = ?",
            (int(str(reply_to_message_id).strip()),),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"reply_to_message_id {reply_to_message_id!r} does not match any message"
            )
        parent_mid = (row["message_id"] or "").strip()
        if not parent_mid:
            raise ValueError(
                f"reply_to_message_id {reply_to_message_id!r} has no Message-ID header"
            )
        parent_refs = (row["ref_list"] or "").strip()
        references = f"{parent_refs} {parent_mid}".strip()
        return _angle(parent_mid), references
    if in_reply_to and str(in_reply_to).strip():
        mid = _angle(str(in_reply_to).strip())
        return mid, mid
    return None, None


def _angle(mid: str) -> str:
    mid = mid.strip()
    if not mid.startswith("<"):
        mid = f"<{mid}"
    if not mid.endswith(">"):
        mid = f"{mid}>"
    return mid


def build_draft_message(
    from_addr: str,
    domain_name: str,
    *,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str = "",
    body_plain: str | None = None,
    body_html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["From"] = from_addr
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject or "(no subject)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<{uuid.uuid4()}@{domain_name}>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    alt = MIMEMultipart("alternative")
    if body_plain:
        alt.attach(MIMEText(body_plain, "plain", "utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))
    if not body_plain and not body_html:
        alt.attach(MIMEText("", "plain", "utf-8"))
    msg.attach(alt)
    return msg


def _imap_connect(account: Any, domain: Any, secret: str):
    from app.modules.mail.services.imap_client import connect_imap, login_imap

    client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
    login_imap(client, account.username, password=secret)
    return client


def _uid_exists(client, folder: str, uid: str) -> bool:
    from app.modules.mail.services.imap_client import fetch_message_uids, select_folder

    select_folder(client, folder)
    found = fetch_message_uids(client, f"UID {uid}")
    return uid.encode() in found


def _delete_uid_verified(client, folder: str, uid: str) -> None:
    from app.modules.mail.services.imap_client import delete_message_by_uid, select_folder

    # STORE/EXPUNGE act on the selected mailbox (RFC 3501 §6.4.6): without
    # a SELECT they are silent no-ops on a real server and the verification
    # below would then reject an otherwise valid delete/replace.
    select_folder(client, folder)
    delete_message_by_uid(client, uid)
    if _uid_exists(client, folder, uid):
        raise DraftDeletionError(f"Draft UID {uid} is still present in {folder} after deletion")


def _delete_draft_from_cache(conn, uid: str, folder: str | None = None) -> None:
    from app.modules.mail.services.cache_db import delete_messages_by_uids

    folders = [folder] if folder else drafts_folder_rows(conn)
    for name in folders:
        delete_messages_by_uids(conn, name, [str(uid)])


def save_draft(
    account: Any,
    domain: Any,
    secret: str,
    *,
    to: list[str] | None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str = "",
    body_plain: str | None = None,
    body_html: str | None = None,
    replace: str | int | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    get_cache_conn: Callable[[], Any] | None = None,
    enqueue_sync: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Create (or replace) a draft via IMAP APPEND with \\Draft \\Seen flags.

    Returns a payload dict; when a cache connection is provided the new
    draft is also synced into the local cache so web views see it
    immediately, and the payload is enriched with the cached fields
    (id, imap_uid, thread_id, snippet, ...).
    """
    from app.modules.mail.services.imap_client import (
        ensure_folder_and_append,
        parse_append_uid,
        safe_logout,
    )

    replace_uid = None
    replace_folder: str | None = None
    if replace is not None and str(replace).strip():
        # U6.5b: identifiers resolve ONLY via the Drafts rows in the mail
        # cache. Treating an unresolvable digit as a raw IMAP UID could
        # delete an unrelated draft when the two ID spaces collide, so a
        # cache miss is a loud DraftNotFoundError (the cache is kept fresh
        # by save_draft itself, which syncs every draft it appends).
        if get_cache_conn is None:
            replace_uid = str(replace).strip()
        else:
            conn = get_cache_conn()
            try:
                row = resolve_draft_identifier(conn, replace)
            finally:
                conn.close()
            if row is not None:
                replace_uid = str(row["uid"])
                # The old draft must be expunged from the folder it
                # actually lives in (which may be an alias like
                # INBOX.Drafts), not the canonical name — otherwise it
                # survives the replace and reappears as a duplicate on the
                # next sync.
                replace_folder = str(row["folder"] or "Drafts")
            else:
                raise DraftNotFoundError(replace)

    msg = build_draft_message(
        account.email_address,
        domain.name,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_plain=body_plain,
        body_html=body_html,
        in_reply_to=in_reply_to,
        references=references,
    )

    client = _imap_connect(account, domain, secret)
    try:
        if replace_uid:
            try:
                _delete_uid_verified(client, replace_folder or "Drafts", replace_uid)
            except Exception:
                # The old draft is being replaced; a failed delete must not
                # silently strand both drafts — log and re-raise so callers
                # report the error instead of appending a duplicate (U6.5c).
                _logger.exception("replace draft deletion failed replace_uid=%s", replace_uid)
                raise
        _, resp_data = ensure_folder_and_append(client, "Drafts", msg.as_bytes(), flags=DRAFT_FLAGS)
        draft_uid = parse_append_uid(resp_data)
    finally:
        safe_logout(client)

    if get_cache_conn is not None:
        _delete_draft_from_cache_by_identifier(get_cache_conn, replace)
        if enqueue_sync is not None:
            enqueue_sync(folder="Drafts", reason="draft_saved", priority=5)

    result: dict[str, Any] = {
        "status": "draft",
        "draft_uid": draft_uid,
        "draft_id": draft_uid,
        "message_id": msg["Message-ID"],
    }
    if get_cache_conn is not None and draft_uid:
        from app.modules.mail.services.message_sync import fetch_and_cache_message

        try:
            row = fetch_and_cache_message(
                account, domain, secret, get_cache_conn, "Drafts", draft_uid
            )
        except Exception:
            _logger.exception("draft cache sync failed account_id=%s uid=%s", account.id, draft_uid)
            row = None
        if row is not None:
            result["draft_id"] = row["id"]
            result["id"] = row["id"]
            result["imap_uid"] = row["uid"]
            result["folder"] = row["folder"]
            result["thread_id"] = row["thread_id"]
            result["snippet"] = row["snippet"] or ""
            result["subject"] = row["subject"] or subject
    return result


def _delete_draft_from_cache_by_identifier(get_cache_conn, replace) -> None:
    if replace is None or not str(replace).strip():
        return
    try:
        conn = get_cache_conn()
        try:
            row = resolve_draft_identifier(conn, replace)
            if row is not None:
                _delete_draft_from_cache(conn, str(row["uid"]), row["folder"])
        finally:
            conn.close()
    except Exception:
        _logger.warning("old draft cache cleanup failed replace=%s", replace, exc_info=True)


def delete_draft(
    account: Any,
    domain: Any,
    secret: str,
    identifier: str | int,
    *,
    get_cache_conn: Callable[[], Any],
    enqueue_sync: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Delete a draft by cache id or IMAP uid, with verification (U6.5c).

    Raises DraftNotFoundError when the identifier matches no draft in the
    cache or on the server.
    """
    ident = str(identifier).strip()
    conn = get_cache_conn()
    try:
        row = resolve_draft_identifier(conn, ident)
    finally:
        conn.close()
    if row is None:
        # U6.5b/U6.5c: identifiers resolve only via the Drafts rows in the
        # cache (a raw-UID fallback could expunge an unrelated draft when
        # the cache-id and UID spaces collide). Unresolvable = 404, never a
        # phantom success. save_draft keeps the cache fresh for every draft
        # it appends, so a real draft is always resolvable.
        raise DraftNotFoundError(ident)
    uid = str(row["uid"])
    folder = str(row["folder"])

    from app.modules.mail.services.imap_client import safe_logout

    client = _imap_connect(account, domain, secret)
    try:
        if not _uid_exists(client, folder, uid):
            # Cache said the draft exists but the server disagrees. Clean
            # the stale cache row and report not-found.
            conn = get_cache_conn()
            try:
                _delete_draft_from_cache(conn, uid, folder)
            finally:
                conn.close()
            raise DraftNotFoundError(ident)
        _delete_uid_verified(client, folder, uid)
    finally:
        safe_logout(client)

    conn = get_cache_conn()
    try:
        _delete_draft_from_cache(conn, uid, folder)
        # U6.5c(b): verify the cache row is actually gone.
        from app.modules.mail.services.cache_db import get_message_by_uid_and_folder

        if get_message_by_uid_and_folder(conn, uid, folder) is not None:
            raise DraftDeletionError(f"Draft cache row for UID {uid} survived deletion in {folder}")
    finally:
        conn.close()
    if enqueue_sync is not None:
        enqueue_sync(folder="Drafts", reason="draft_deleted", priority=5)

    return {
        "status": "deleted",
        "draft_uid": uid,
        "draft_id": row["id"] if row is not None else uid,
        "folder": folder,
    }
