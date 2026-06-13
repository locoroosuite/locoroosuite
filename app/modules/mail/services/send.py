from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any, Callable

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
    get_cache_conn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    _build_and_send_smtp(account, domain, secret, to, cc, bcc, subject, body_plain, body_html)
    sent_msg, sent_uid, msg_id = _append_to_sent(account, domain, secret, to, cc, bcc, subject, body_plain, body_html)
    if draft_id:
        _cleanup_draft(account, domain, secret, draft_id, get_cache_conn)
    return {
        "status": "sent",
        "message_id": msg_id,
        "subject": subject,
        "from": account.email_address,
        "to": to,
        "cc": cc or [],
        "sent_uid": sent_uid,
    }


def _build_message(to, cc, bcc, subject, body_plain, body_html, from_addr, domain_name, for_sent=False):
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
    alt = MIMEMultipart("alternative")
    if body_plain:
        alt.attach(MIMEText(body_plain, "plain", "utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))
    if not body_plain and not body_html:
        alt.attach(MIMEText("", "plain", "utf-8"))
    msg.attach(alt)
    return msg


def _build_and_send_smtp(account, domain, secret, to, cc, bcc, subject, body_plain, body_html):
    from app.modules.mail.services.smtp_client import smtp_connect, smtp_login, smtp_send

    msg = _build_message(to, cc, bcc, subject, body_plain, body_html, account.email_address, domain.name)
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


def _append_to_sent(account, domain, secret, to, cc, bcc, subject, body_plain, body_html):
    from app.modules.mail.services.imap_client import ensure_folder_and_append, parse_append_uid, safe_logout

    sent_msg = _build_message(
        to, cc, bcc, subject, body_plain, body_html,
        account.email_address, domain.name, for_sent=True,
    )
    sent_msg["Date"] = formatdate(localtime=True)

    sent_uid = None
    try:
        client = _imap_connect(account, domain, secret)
        try:
            _, resp_data = ensure_folder_and_append(client, "Sent", sent_msg.as_bytes(), flags=["\\Seen"])
            sent_uid = parse_append_uid(resp_data)
        finally:
            safe_logout(client)
    except Exception:
        _logger.warning("append to Sent folder failed", exc_info=True)

    return sent_msg, sent_uid, sent_msg["Message-ID"]


def _cleanup_draft(account, domain, secret, draft_id, get_cache_conn):
    from app.modules.mail.services.imap_client import delete_message_by_uid, safe_logout, select_folder

    try:
        client = _imap_connect(account, domain, secret)
        try:
            select_folder(client, "Drafts")
            delete_message_by_uid(client, str(draft_id))
        finally:
            safe_logout(client)
    except Exception:
        _logger.warning("draft IMAP cleanup failed draft_id=%s", draft_id, exc_info=True)

    if get_cache_conn:
        try:
            from app.modules.mail.services.cache_db import delete_messages_by_uids

            conn = get_cache_conn()
            try:
                delete_messages_by_uids(conn, "Drafts", [str(draft_id)])
            finally:
                conn.close()
        except Exception:
            _logger.warning("draft cache cleanup failed draft_id=%s", draft_id, exc_info=True)


def _imap_connect(account, domain, secret):
    from app.modules.mail.services.imap_client import connect_imap, login_imap

    client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
    login_imap(client, account.username, password=secret)
    return client
