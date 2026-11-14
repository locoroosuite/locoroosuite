from __future__ import annotations

import logging
from collections.abc import Callable
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

_logger = logging.getLogger(__name__)


def send_message(
    account: Any,
    domain: Any,
    secret: str,
    *,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str = "",
    body_plain: str | None = None,
    body_html: str | None = None,
    draft_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    get_cache_conn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    # U6.5b: resolve a cache-id draft reference to its IMAP UID before the
    # send so cleanup targets the right message; a stale/unknown identifier
    # is reported honestly afterwards instead of failing the send (the mail
    # is already out — a resend would duplicate it).
    resolved_draft_uid = str(draft_id) if draft_id else None
    draft_error = None
    if draft_id and get_cache_conn:
        from app.modules.mail.services.drafts import resolve_draft_identifier

        conn = get_cache_conn()
        try:
            row = resolve_draft_identifier(conn, draft_id)
        finally:
            conn.close()
        if row is not None:
            resolved_draft_uid = str(row["uid"])
        else:
            resolved_draft_uid = None
            draft_error = "DRAFT_NOT_FOUND"

    _build_and_send_smtp(
        account,
        domain,
        secret,
        to,
        cc,
        bcc,
        subject,
        body_plain,
        body_html,
        in_reply_to,
        references,
    )
    _sent_msg, sent_uid, msg_id = _append_to_sent(
        account,
        domain,
        secret,
        to,
        cc,
        bcc,
        subject,
        body_plain,
        body_html,
        in_reply_to,
        references,
    )
    draft_deleted: bool | None = None
    if draft_id:
        if resolved_draft_uid:
            try:
                _cleanup_draft(account, domain, secret, resolved_draft_uid, get_cache_conn)
                draft_deleted = True
            except Exception:
                _logger.exception(
                    "draft cleanup failed draft_id=%s uid=%s", draft_id, resolved_draft_uid
                )
                draft_deleted = False
                draft_error = "DRAFT_DELETE_FAILED"
        else:
            draft_deleted = False
    # U4.7f: notify SSE listeners so open folder/conversation views refresh.
    try:
        from app.shared.ui_events import push_ui_event

        push_ui_event(account.customer_id, "mail", "send_complete", {"account_id": account.id})
    except Exception:
        _logger.debug("send ui_change push failed account_id=%s", account.id, exc_info=True)
    result = {
        "status": "sent",
        "message_id": msg_id,
        "subject": subject,
        "from": account.email_address,
        "to": to,
        "cc": cc or [],
        "sent_uid": sent_uid,
    }
    # U6.5c(c): report the delete-after-send outcome, never swallow it.
    if draft_id:
        result["draft_deleted"] = draft_deleted
        if draft_error:
            result["draft_error"] = draft_error
    return result


def _build_message(
    to,
    cc,
    bcc,
    subject,
    body_plain,
    body_html,
    from_addr,
    domain_name,
    for_sent=False,
    in_reply_to=None,
    references=None,
):
    msg = MIMEMultipart("mixed")
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if for_sent and bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    if for_sent:
        msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<{__import__('uuid').uuid4()}@{domain_name}>"
    # U15.26: reply threading headers — present on both the SMTP copy and
    # the Sent copy so the reply groups with the source conversation (U4.7).
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


def _build_and_send_smtp(
    account,
    domain,
    secret,
    to,
    cc,
    bcc,
    subject,
    body_plain,
    body_html,
    in_reply_to=None,
    references=None,
):
    from app.modules.mail.services.smtp_client import smtp_connect, smtp_login, smtp_send

    msg = _build_message(
        to,
        cc,
        bcc,
        subject,
        body_plain,
        body_html,
        account.email_address,
        domain.name,
        in_reply_to=in_reply_to,
        references=references,
    )
    all_recipients = list(to) + (cc or []) + (bcc or [])
    msg_bytes = msg.as_bytes()

    tls_mode = domain.smtp_tls_mode or "starttls"
    server = smtp_connect(domain.smtp_host, domain.smtp_port, tls_mode)
    try:
        smtp_login(server, account.username, password=secret)
        smtp_send(server, account.email_address, all_recipients, msg_bytes)
    finally:
        try:
            server.quit()
        except Exception:
            _logger.debug("SMTP quit failed", exc_info=True)


def _append_to_sent(
    account,
    domain,
    secret,
    to,
    cc,
    bcc,
    subject,
    body_plain,
    body_html,
    in_reply_to=None,
    references=None,
):
    from app.modules.mail.services.imap_client import (
        ensure_folder_and_append,
        parse_append_uid,
        safe_logout,
    )

    sent_msg = _build_message(
        to,
        cc,
        bcc,
        subject,
        body_plain,
        body_html,
        account.email_address,
        domain.name,
        for_sent=True,
        in_reply_to=in_reply_to,
        references=references,
    )
    sent_msg["Date"] = formatdate(localtime=True)

    sent_uid = None
    try:
        client = _imap_connect(account, domain, secret)
        try:
            _, resp_data = ensure_folder_and_append(
                client, "Sent", sent_msg.as_bytes(), flags=["\\Seen"]
            )
            sent_uid = parse_append_uid(resp_data)
        finally:
            safe_logout(client)
    except Exception:
        _logger.warning("append to Sent folder failed", exc_info=True)

    return sent_msg, sent_uid, sent_msg["Message-ID"]


def _cleanup_draft(account, domain, secret, draft_id, get_cache_conn):
    """Delete the draft after a successful send. Raises on failure so the
    caller can report draft_deleted=false (U6.5c) instead of swallowing."""
    from app.modules.mail.services.imap_client import (
        delete_message_by_uid,
        safe_logout,
        select_folder,
    )

    client = _imap_connect(account, domain, secret)
    try:
        select_folder(client, "Drafts")
        delete_message_by_uid(client, str(draft_id))
    finally:
        safe_logout(client)

    if get_cache_conn:
        from app.modules.mail.services.cache_db import delete_messages_by_uids

        conn = get_cache_conn()
        try:
            delete_messages_by_uids(conn, "Drafts", [str(draft_id)])
        finally:
            conn.close()


def _imap_connect(account, domain, secret):
    from app.modules.mail.services.imap_client import connect_imap, login_imap

    client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
    login_imap(client, account.username, password=secret)
    return client
