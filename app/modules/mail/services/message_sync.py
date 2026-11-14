"""Single-message IMAP fetch → mail-cache upsert.

Shared by the REST API, the MCP tools, and the draft service so a message
that was just APPENDed (draft or Sent copy) is visible to web UI views
immediately, without waiting for the next folder sync (U4.7e, U6.5a).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

_logger = logging.getLogger(__name__)


def fetch_and_cache_message(
    account: Any,
    domain: Any,
    secret: str,
    get_cache_conn: Callable[[], Any],
    folder: str,
    uid: str | int,
) -> Any:
    """Fetch one message from IMAP and upsert it into the mail cache.

    Returns the fresh cache row (sqlite3.Row) or None when the message
    cannot be fetched (e.g. already expunged). Never raises on a missing
    message; IMAP connection failures propagate to the caller.
    """
    from app.modules.mail.services.cache_db import (
        get_message_by_uid_and_folder,
        upsert_message,
    )
    from app.modules.mail.services.imap_client import (
        connect_imap,
        fetch_message_with_flags,
        login_imap,
        safe_logout,
        select_folder,
    )
    from app.modules.mail.services.imap_sync import _prepare_message_args, _to_uid_str

    uid_str = _to_uid_str(uid)
    client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
    login_imap(client, account.username, password=secret)
    try:
        select_folder(client, folder)
        msg, flags, internal_date = fetch_message_with_flags(client, uid_str)
    finally:
        safe_logout(client)
    if not msg:
        return None
    args = _prepare_message_args(msg, account=account)
    conn = get_cache_conn()
    try:
        upsert_message(
            conn,
            uid_str,
            folder,
            args["subject"],
            args["sender"],
            args["recipients"],
            args["date"],
            flags,
            args["snippet"],
            args["body"],
            args["has_attachments"],
            args["message_id"],
            thread_id=args["thread_id"],
            in_reply_to=args["in_reply_to"],
            ref_list=args["ref_list"],
            internal_date=internal_date,
            is_bounce=args["is_bounce"],
            bounce_reason=args["bounce_reason"],
            original_subject=args["original_subject"],
            cc=args["cc"],
            attachment_list=args["attachment_list"],
            body_html=args["body_html"],
        )
        row = get_message_by_uid_and_folder(conn, uid_str, folder)
    finally:
        conn.close()
    return row


def sent_copy_summary(row) -> dict | None:
    """Summary fields for a just-cached Sent copy (shared by REST + MCP
    send paths so the send response shape cannot drift)."""
    if row is None:
        return None
    return {
        "id": row["id"],
        "imap_uid": row["uid"],
        "folder": row["folder"],
        "thread_id": row["thread_id"],
        "snippet": row["snippet"] or "",
        "date": row["date"] or "",
        "flags": row["flags"] or "",
    }
