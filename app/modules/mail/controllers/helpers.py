import logging
import threading
import uuid
import json
import time
import imaplib
import re
import math
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, session, request, url_for, current_app

from app.shared.db import db
from app.shared.models.core import Domain, CustomerAccount, CustomerSettings
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.imap_client import (
    connect_imap, login_imap, select_folder, fetch_message,
    set_flag, append_message, list_folders, safe_logout, create_folder,
    ensure_folder_and_append,
    delete_message_by_uid,
)
from app.modules.mail.services.smtp_client import smtp_connect, smtp_login, smtp_send
from app.modules.mail.services.cache_db import open_cache, list_messages, get_message, update_flags, delete_messages_by_uids
from app.modules.mail.services.folder_sort import build_folder_sections
from app.shared.keys import get_user_key
from app.modules.mail.utils.sanitize import (
    decode_address_header, normalize_header_text, normalize_preview_text,
    plain_text_to_html, sanitize_html, strip_subject_from_html,
    strip_subject_from_text, wrap_email_html, add_quoted_collapse,
)
from app.shared.icalendar import parse_icalendar


mail_bp = Blueprint("mail", __name__, template_folder="../templates")
mail_sse_bp = Blueprint("mail_sse", __name__)
logger = logging.getLogger(__name__)


@mail_bp.context_processor
def _inject_unread_excluded_folders():
    from app.modules.mail.services.folder_sort import UNREAD_EXCLUDED_FOLDERS
    return {"unread_excluded_folders": [f.lower() for f in sorted(UNREAD_EXCLUDED_FOLDERS)]}

_pending_sends = {}
_pending_sends_lock = threading.RLock()
_send_failure_notice = {}
_SEND_RECORD_TTL_SECONDS = 60 * 60
_UNDO_SECONDS = 8


def _imap_for_account(account, secret, domain=None):
    domain = domain or db.session.get(Domain, account.domain_id)
    client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
    login_imap(client, account.username, password=secret)
    return client, domain


def _get_or_create_settings(user_id):
    settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
    if not settings:
        settings = CustomerSettings(customer_id=user_id)
        db.session.add(settings)
        db.session.commit()
    return settings


def _load_spam_action_prefs(settings):
    if not settings or not settings.spam_action_prefs:
        return {}
    try:
        prefs = json.loads(settings.spam_action_prefs)
        return prefs if isinstance(prefs, dict) else {}
    except (TypeError, ValueError):
        return {}


def _spam_action_enabled(settings, account_id):
    prefs = _load_spam_action_prefs(settings)
    return prefs.get(str(account_id), True)


def _set_spam_action_enabled(settings, account_id, enabled):
    prefs = _load_spam_action_prefs(settings)
    prefs[str(account_id)] = bool(enabled)
    settings.spam_action_prefs = json.dumps(prefs)


def _fallback_sidebar_folders(conn, cached_folders):
    names = list((cached_folders or {}).keys())
    try:
        rows = conn.execute(
            "SELECT DISTINCT folder FROM messages WHERE folder IS NOT NULL AND folder != '' ORDER BY folder COLLATE NOCASE ASC"
        ).fetchall()
        names.extend([row[0] for row in rows if row and row[0]])
    except Exception:
        logger.exception("folder sidebar cache folder discovery failed")
    deduped = []
    seen = set()
    for name in names:
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    if "inbox" not in seen:
        deduped.insert(0, "INBOX")
        return deduped
    for idx, name in enumerate(deduped):
        if name.lower() == "inbox":
            inbox_name = deduped.pop(idx)
            deduped.insert(0, inbox_name)
            break
    return deduped


def _folder_sidebar_context(user_id, account, key, conn):
    from app.modules.mail.services.cache_db import list_cached_folders
    from app.modules.mail.services.cache_db import count_unread_flagged

    cached_folders = dict(list_cached_folders(conn))
    starred_count = count_unread_flagged(conn)
    folders = None
    sidebar_warning = None
    try:
        secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
        client, _domain = _imap_for_account(account, secret)
        try:
            folders = list_folders(client)
        finally:
            safe_logout(client)
    except imaplib.IMAP4.error:
        logger.exception(
            "folder sidebar imap auth failed account_id=%s customer_id=%s",
            account.id,
            user_id,
        )
        folders = _fallback_sidebar_folders(conn, cached_folders)
        sidebar_warning = "IMAP is temporarily unavailable. Showing cached data while retrying in the background."
    except Exception:
        logger.exception(
            "folder sidebar imap fetch failed account_id=%s customer_id=%s",
            account.id,
            user_id,
        )
        folders = _fallback_sidebar_folders(conn, cached_folders)
        sidebar_warning = "IMAP is temporarily unavailable. Showing cached data while retrying in the background."
    settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
    pinned = []
    if settings and settings.pinned_folders:
        try:
            pinned = json.loads(settings.pinned_folders)
        except (TypeError, ValueError):
            pinned = []
    folder_sections = build_folder_sections(folders, pinned, conn)
    accounts = CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).all()
    return accounts, folder_sections, cached_folders, pinned, starred_count, sidebar_warning


def _parse_flags(raw_flags):
    if not raw_flags:
        return []
    if isinstance(raw_flags, (list, tuple)):
        return list(raw_flags)
    try:
        parsed = json.loads(raw_flags)
        if isinstance(parsed, (list, tuple)):
            return list(parsed)
        return []
    except (TypeError, ValueError):
        return []


def _uid_to_str(uid):
    if isinstance(uid, bytes):
        return uid.decode(errors="ignore")
    return str(uid)


def _decode_part(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="ignore")
        except (LookupError, TypeError):
            return payload.decode(errors="ignore")
    return str(payload)


def _is_attachment_part(part):
    disposition = part.get_content_disposition()
    if disposition == "attachment":
        return True
    content_type = part.get_content_type()
    if content_type.startswith("multipart/"):
        return False
    if content_type in ("text/plain", "text/html"):
        return False
    if part.get_filename():
        return True
    return False


def _fetch_attachment_bytes(account, message_id, index):
    from app.modules.mail.services.imap_client import fetch_message as imap_fetch_message
    from app.shared.keys import get_user_key

    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    message = get_message(conn, message_id)
    if not message:
        return None, None, None
    uid = message["uid"]
    folder = message["folder"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    raw_msg = imap_fetch_message(client, uid)
    client.logout()
    if not raw_msg:
        return None, None, None
    attachments = []
    for part in raw_msg.walk():
        if _is_attachment_part(part):
            attachments.append(part)
    if index >= len(attachments):
        return None, None, None
    part = attachments[index]
    filename = normalize_header_text(part.get_filename()) or f"attachment-{index}"
    data = part.get_payload(decode=True)
    return filename, data, part.get_content_type()


def _extract_message_bodies(message):
    text_plain = ""
    text_html = ""
    if message is None:
        return text_plain, text_html
    if message.is_multipart():
        for part in message.walk():
            if _is_attachment_part(part):
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain" and not text_plain:
                text_plain = _decode_part(part)
            elif content_type == "text/html" and not text_html:
                text_html = _decode_part(part)
    else:
        content_type = message.get_content_type()
        if content_type == "text/html":
            text_html = _decode_part(message)
        else:
            text_plain = _decode_part(message)
    return text_plain, text_html


def _parse_message_datetime(date_value):
    if not date_value:
        return None
    if isinstance(date_value, datetime):
        dt = date_value
    else:
        try:
            dt = parsedate_to_datetime(date_value)
        except (TypeError, ValueError, IndexError):
            return None
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_timezone(timezone_name):
    from app.shared.timezone import resolve_tzinfo
    return resolve_tzinfo(timezone_name)


def _message_date_ts(date_value):
    dt = _parse_message_datetime(date_value)
    if not dt:
        return None
    return int(dt.timestamp())


def _decorate_message_row(row, timezone_name=None, is_sent=False):
    flags = _parse_flags(row["flags"])
    subject = normalize_header_text(row["subject"]) or "(no subject)"
    sender_full = decode_address_header(row["sender"])
    sender_display, sender_tooltip = _format_sender(sender_full)
    body = row["body"]
    snippet = normalize_preview_text(row["snippet"], limit=500, fallback=body)
    date_ts = _message_date_ts(row["date"])
    sort_ts = row["sort_ts"] if row["sort_ts"] is not None else date_ts
    folder = row["folder"]
    thread_id = row["thread_id"]
    recipients_raw = row["recipients"]
    is_bounce = bool(row["is_bounce"])
    bounce_reason = row["bounce_reason"]
    original_subject = normalize_header_text(row["original_subject"]) if row["original_subject"] else None
    has_attachments = bool(row["has_attachments"])
    is_draft = "\\Draft" in flags or (folder and folder.lower() == "drafts")
    recipients_display = ""
    if recipients_raw:
        from email.utils import getaddresses as _getaddresses
        addrs = _getaddresses([decode_address_header(recipients_raw)])
        recipients_display = ", ".join(name or addr for name, addr in addrs[:2])
    display_subject = original_subject or subject
    return {
        "id": row["id"],
        "subject": display_subject,
        "sender": sender_full,
        "sender_display": sender_display or sender_full,
        "sender_tooltip": sender_tooltip or sender_full,
        "snippet": snippet,
        "date": row["date"],
        "date_ts": date_ts,
        "sort_ts": sort_ts,
        "date_display": _format_short_date(row["date"], timezone_name),
        "flags": flags,
        "is_unread": "\\Seen" not in flags,
        "is_flagged": "\\Flagged" in flags,
        "folder": folder,
        "thread_id": thread_id,
        "is_sent": is_sent,
        "is_draft": is_draft,
        "recipients_display": recipients_display,
        "is_bounce": is_bounce,
        "bounce_reason": bounce_reason,
        "has_attachments": has_attachments,
    }


def _format_sender(sender_raw):
    if not sender_raw:
        return "", ""
    addresses = getaddresses([sender_raw])
    if addresses:
        name, addr = addresses[0]
        name = normalize_header_text(name)
        addr = normalize_header_text(addr)
        if name:
            words = [word for word in re.split(r"\s+", name) if word]
            display = " ".join(words[:2]) if words else name
            full = f"{name} <{addr}>" if addr else name
            return display, full
        if addr:
            local = addr.split("@")[0]
            return local, addr
    cleaned = normalize_header_text(sender_raw)
    if "@" in cleaned:
        return cleaned.split("@")[0], cleaned
    return cleaned, cleaned


def _format_short_date(date_value, timezone_name=None):
    if not date_value:
        return ""
    dt = _parse_message_datetime(date_value)
    if not dt:
        return date_value
    tz = _resolve_timezone(timezone_name)
    local_dt = dt.astimezone(tz)
    now = datetime.now(tz)
    if now - timedelta(hours=24) <= local_dt <= now:
        return local_dt.strftime("%H:%M")
    if local_dt.year == now.year:
        return local_dt.strftime("%d %b %H:%M")
    return local_dt.strftime("%d %b %y %H:%M")


def normalize_subject_for_threading(subject):
    if not subject:
        return ""
    s = subject.strip()
    while True:
        prev = s
        s = re.sub(r'^\[[^\]]*\]\s*', '', s)
        s = re.sub(r'^(Re|Fwd|Fw)\s*:\s*', '', s, flags=re.IGNORECASE)
        s = s.strip()
        if s == prev:
            break
    return re.sub(r'\s+', ' ', s).lower().strip()


def _build_threads(conn, folder, timezone_name=None, account_email=None, page=1, per_page=50):
    from app.modules.mail.services.cache_db import (
        list_messages_for_folder_view, list_sent_for_threading, list_drafts_for_threading, count_messages_in_folder,
    )

    total_messages = count_messages_in_folder(conn, folder)
    messages = list_messages_for_folder_view(conn, folder)

    decorated = []
    for msg in messages:
        row = _decorate_message_row(msg, timezone_name=timezone_name)
        decorated.append(row)

    groups = {}
    tid_subjects = {}
    subj_to_tid = {}
    for row in decorated:
        tid = row.get("thread_id")
        if tid:
            groups.setdefault(tid, []).append(row)
            subj = normalize_subject_for_threading(row["subject"])
            tid_subjects.setdefault(tid, set()).add(subj)
            subj_to_tid.setdefault(subj, tid)

    for row in decorated:
        if row.get("thread_id"):
            continue
        subj = normalize_subject_for_threading(row["subject"])
        if subj in subj_to_tid:
            groups[subj_to_tid[subj]].append(row)
        else:
            groups.setdefault(f"sub:{subj}", []).append(row)

    _subj_to_primary_key = {}
    for key in list(groups.keys()):
        rows = groups.get(key)
        if not rows:
            continue
        primary = None
        for r in rows:
            rsubj = normalize_subject_for_threading(r["subject"])
            if rsubj in _subj_to_primary_key:
                primary = _subj_to_primary_key[rsubj]
                break
        if primary is not None:
            groups[primary].extend(groups.pop(key))
            for r in rows:
                rsubj = normalize_subject_for_threading(r["subject"])
                if rsubj not in _subj_to_primary_key:
                    _subj_to_primary_key[rsubj] = primary
        else:
            for r in rows:
                rsubj = normalize_subject_for_threading(r["subject"])
                if rsubj not in _subj_to_primary_key:
                    _subj_to_primary_key[rsubj] = key

    subj_to_key = {}
    for key, rows in groups.items():
        for r in rows:
            subj = normalize_subject_for_threading(r["subject"])
            subj_to_key.setdefault(subj, key)

    folder_lower = (folder or "").lower()
    skip_sent_merge = folder_lower == "drafts"
    skip_draft_merge = folder_lower == "drafts"

    sent_messages = [] if skip_sent_merge else list_sent_for_threading(conn)
    if sent_messages and account_email:
        for msg in sent_messages:
            srow = _decorate_message_row(msg, timezone_name=timezone_name, is_sent=True)
            tid = srow.get("thread_id")
            matched_key = None
            if tid and tid in groups:
                matched_key = tid
            if not matched_key:
                subj = normalize_subject_for_threading(srow["subject"])
                if subj in subj_to_key:
                    matched_key = subj_to_key[subj]
            if matched_key:
                existing_ids = {r["id"] for r in groups[matched_key]}
                if srow["id"] not in existing_ids:
                    groups[matched_key].append(srow)

    draft_messages = [] if skip_draft_merge else list_drafts_for_threading(conn)
    if draft_messages and account_email:
        for msg in draft_messages:
            drow = _decorate_message_row(msg, timezone_name=timezone_name)
            drow["is_draft"] = True
            tid = drow.get("thread_id")
            matched_key = None
            if tid and tid in groups:
                matched_key = tid
            if not matched_key:
                subj = normalize_subject_for_threading(drow["subject"])
                if subj in subj_to_key:
                    matched_key = subj_to_key[subj]
            if matched_key:
                existing_ids = {r["id"] for r in groups[matched_key]}
                if drow["id"] not in existing_ids:
                    groups[matched_key].append(drow)

    for key in groups:
        groups[key].sort(key=lambda r: r.get("sort_ts") or 0, reverse=True)

    sorted_threads = dict(sorted(
        groups.items(),
        key=lambda item: max(r.get("sort_ts") or 0 for r in item[1]),
        reverse=True,
    ))

    total_threads = len(sorted_threads)
    total_pages = max(1, math.ceil(total_threads / per_page))
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    page_keys = list(sorted_threads.keys())[start:start + per_page]
    page_threads = {k: sorted_threads[k] for k in page_keys}

    return page_threads, {
        "total_threads": total_threads,
        "total_messages": total_messages,
        "current_page": page,
        "total_pages": total_pages,
        "per_page": per_page,
    }


def _current_undo_action(consume_ephemeral=True):
    action = session.get("undo_action")
    if not action:
        return None
    expires_at = action.get("expires_at")
    if expires_at and expires_at < time.time():
        session.pop("undo_action", None)
        return None
    if consume_ephemeral and action.get("ephemeral"):
        if action.get("shown_once"):
            session.pop("undo_action", None)
            return None
        action["shown_once"] = True
        session["undo_action"] = action
    return action


def _set_undo_action(
    account_id,
    source_folder,
    destination_folder,
    message_id_header,
    label,
    *,
    action_type,
    expires_at=None,
    view_url=None,
    view_label=None,
    ephemeral=False,
    shown_once=False,
):
    token = uuid.uuid4().hex
    session["undo_action"] = {
        "token": token,
        "account_id": account_id,
        "source_folder": source_folder,
        "destination_folder": destination_folder,
        "message_id": message_id_header,
        "label": label,
        "action_type": action_type,
        "expires_at": expires_at,
        "view_url": view_url,
        "view_label": view_label,
        "ephemeral": ephemeral,
        "shown_once": shown_once,
    }
    session.pop("undo_error", None)
    return token


def _cleanup_pending_sends(now_ts=None):
    now_ts = now_ts or time.time()
    with _pending_sends_lock:
        expired_tokens = []
        for token, payload in _pending_sends.items():
            state = payload.get("status")
            if state not in ("sent", "failed", "cancelled"):
                continue
            finished_at = payload.get("updated_at") or payload.get("created_at") or now_ts
            if now_ts - finished_at > _SEND_RECORD_TTL_SECONDS:
                expired_tokens.append(token)
        for token in expired_tokens:
            _pending_sends.pop(token, None)
        for customer_id, token in list(_send_failure_notice.items()):
            if token not in _pending_sends:
                _send_failure_notice.pop(customer_id, None)


def _send_error_message(exc):
    if isinstance(exc, imaplib.IMAP4.error):
        return "IMAP rejected this request while finalizing send. Retry or check account settings."
    message = str(exc or "").strip().lower()
    if "auth" in message:
        return "Mail server authentication failed. Retry or verify account credentials."
    if "timed out" in message or "timeout" in message:
        return "Connection timed out while sending. Retry, check connection, or refresh."
    if "refused" in message or "unreachable" in message or "service not known" in message:
        return "Mail server is temporarily unreachable. Retry in a moment."
    if "sending limit" in message or "daily sending" in message:
        return "Daily sending limit reached. Contact support to increase your quota."
    return "Unable to send this message right now. Retry, check connection, or refresh."


def _send_status_snapshot(send_token, payload, now_ts=None):
    now_ts = now_ts or time.time()
    state = payload.get("status") or "unknown"
    send_after = payload.get("send_after") or now_ts
    remaining_seconds = max(0, int(math.ceil(send_after - now_ts))) if state == "countdown" else 0
    return {
        "token": send_token,
        "state": state,
        "seconds_remaining": remaining_seconds,
        "can_send_now": state == "countdown" and remaining_seconds > 0,
        "can_undo": state in ("countdown", "queued"),
        "can_retry": state == "failed",
        "error": payload.get("error"),
        "warning": payload.get("warning"),
        "account_id": payload.get("account_id"),
        "open_draft_url": url_for(
            "mail.compose",
            account_id=payload.get("account_id"),
            prefill_token=send_token,
        ),
    }


def _consume_send_failure_notice(customer_id):
    with _pending_sends_lock:
        token = _send_failure_notice.pop(customer_id, None)
        if not token:
            return None
        payload = _pending_sends.get(token)
        if not payload or payload.get("status") != "failed":
            return None
        return {
            "token": token,
            "account_id": payload.get("account_id"),
            "error": payload.get("error") or "Message failed to send.",
        }


def _start_send_worker(send_token, delay_seconds=0):
    app = current_app._get_current_object()
    threading.Thread(
        target=_send_worker,
        args=(app, send_token, delay_seconds),
        daemon=True,
    ).start()


def _send_worker(app, send_token, delay_seconds=0):
    if delay_seconds:
        threading.Event().wait(delay_seconds)

    with app.app_context():
        payload = None
        with _pending_sends_lock:
            payload = _pending_sends.get(send_token)
            if not payload:
                return
            if payload.get("status") not in ("countdown", "queued"):
                return
            payload["status"] = "sending"
            payload["error"] = None
            payload["warning"] = None
            payload["updated_at"] = time.time()

        try:
            account = db.session.get(CustomerAccount, payload["account_id"])
            if not account or not account.is_active:
                raise RuntimeError("Account is unavailable.")
            domain = db.session.get(Domain, payload["domain_id"])
            if not domain or not domain.is_active:
                raise RuntimeError("Domain is unavailable.")

            secret = payload.get("secret")
            recipients = []
            seen = set()
            for field in (payload.get("to_addrs"), payload.get("cc_addrs"), payload.get("bcc_addrs")):
                if not field:
                    continue
                for _, addr in getaddresses([field]):
                    addr = addr.strip()
                    if addr and addr.lower() not in seen:
                        seen.add(addr.lower())
                        recipients.append(addr)
            if not recipients:
                raise RuntimeError("At least one recipient is required.")

            server = None
            try:
                server = smtp_connect(domain.smtp_host, domain.smtp_port, domain.smtp_tls_mode)
                smtp_login(
                    server,
                    payload["from_addr"],
                    password=secret,
                )
                smtp_send(server, payload["from_addr"], recipients, payload["msg"])
            finally:
                if server:
                    try:
                        server.quit()
                    except Exception:
                        pass

            warning = None
            imap_client = None
            try:
                imap_client, _domain = _imap_for_account(account, secret, domain=domain)
                ensure_folder_and_append(imap_client, "Sent", payload.get("sent_msg") or payload["msg"])
            except Exception:
                warning = "Message sent, but saving to Sent failed."
                logger.exception("send sent-copy append failed send_token=%s account_id=%s", send_token, account.id)
            finally:
                if imap_client:
                    safe_logout(imap_client)

            draft_uid = payload.get("draft_uid")
            if draft_uid:
                draft_client = None
                try:
                    draft_client, _domain = _imap_for_account(account, secret, domain=domain)
                    select_folder(draft_client, "Drafts")
                    delete_message_by_uid(draft_client, draft_uid)
                except Exception:
                    logger.debug("failed to delete draft after send uid=%s", draft_uid, exc_info=True)
                finally:
                    if draft_client:
                        safe_logout(draft_client)
                try:
                    conn = open_cache(account.cache_db_path, get_user_key(payload["user_id"]))
                    delete_messages_by_uids(conn, "Drafts", [draft_uid])
                except Exception:
                    logger.debug("failed to delete draft from cache after send uid=%s", draft_uid, exc_info=True)

            with _pending_sends_lock:
                latest = _pending_sends.get(send_token)
                if not latest:
                    return
                latest["status"] = "sent"
                latest["warning"] = warning
                latest["error"] = None
                latest["sent_at"] = time.time()
                latest["updated_at"] = latest["sent_at"]
                if _send_failure_notice.get(latest["user_id"]) == send_token:
                    _send_failure_notice.pop(latest["user_id"], None)

            try:
                app.sync_manager.enqueue_sync(account.id, folder="Sent", reason="send_complete", priority=5)
            except Exception:
                logger.debug("sent-folder sync enqueue failed send_token=%s", send_token)
            try:
                app.sync_manager.enqueue_sync(account.id, folder="Drafts", reason="send_complete", priority=5)
            except Exception:
                logger.debug("drafts-folder sync enqueue failed send_token=%s", send_token)
        except Exception as exc:
            logger.exception("send worker failed send_token=%s", send_token)
            error_text = _send_error_message(exc)
            with _pending_sends_lock:
                latest = _pending_sends.get(send_token)
                if not latest:
                    return
                latest["status"] = "failed"
                latest["error"] = error_text
                latest["warning"] = None
                latest["failed_at"] = time.time()
                latest["updated_at"] = latest["failed_at"]
                _send_failure_notice[latest["user_id"]] = send_token
        finally:
            _cleanup_pending_sends()


def _rewrite_cid_urls(html, cid_map, account_id, message_id):
    if not cid_map or not html:
        return html

    def _replace_cid(m):
        cid = m.group(1)
        if cid in cid_map:
            path = url_for(
                "mail.download_attachment",
                account_id=account_id,
                message_id=message_id,
                index=cid_map[cid],
            )
            return f'src="{path}?inline=1"'
        return m.group(0)

    html = re.sub(r'src="cid:([^"]+)"', _replace_cid, html, flags=re.IGNORECASE)
    html = re.sub(r"src='cid:([^']+)'", _replace_cid, html, flags=re.IGNORECASE)
    return html


def _load_message_detail(account, message_id, allow_images=False, mark_seen=True, collapse_quotes=False):
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    message = get_message(conn, message_id)
    if not message:
        return None, None, None, None, None, None, ""
    message = dict(message)
    message["subject"] = normalize_header_text(message["subject"]) or "(no subject)"
    message["sender"] = decode_address_header(message["sender"])
    message["recipients"] = decode_address_header(message["recipients"])
    cached_cc = decode_address_header(message["cc"]) if message["cc"] else ""
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    if mark_seen:
        set_flag(client, uid, "\\Seen", add=True)
        flags = [flag for flag in flags if flag != "\\Seen"] + ["\\Seen"]
        update_flags(conn, message_id, flags)
    raw_msg = fetch_message(client, uid)
    live_cc = ""
    if raw_msg:
        cc_header = raw_msg.get("Cc", "") or ""
        live_cc = decode_address_header(cc_header) if cc_header else ""
    cc_display = cached_cc or live_cc
    attachments = []
    ics_attachments = []
    cid_map = {}
    if raw_msg and raw_msg.is_multipart():
        for part in raw_msg.walk():
            content_type = part.get_content_type()
            filename = part.get_filename()
            is_ics = (
                content_type in ("text/calendar", "application/ics")
                or (filename and filename.lower().endswith(".ics"))
            )
            if is_ics:
                ics_text = _decode_part(part)
                if ics_text:
                    parsed_ics = parse_icalendar(ics_text)
                    if parsed_ics:
                        ics_attachments.append({
                            "index": len(ics_attachments),
                            "filename": normalize_header_text(filename) or f"invite-{len(ics_attachments)}.ics",
                            "ical_text": ics_text,
                            "parsed": parsed_ics,
                        })
                        continue
            if _is_attachment_part(part):
                content_id = part.get("Content-ID", "")
                if content_id:
                    content_id = content_id.strip("<>")
                    cid_map[content_id] = len(attachments)
                attachments.append({
                    "index": len(attachments),
                    "filename": normalize_header_text(filename) or f"attachment-{len(attachments)}",
                })
    elif raw_msg:
        content_type = raw_msg.get_content_type()
        if content_type in ("text/calendar", "application/ics"):
            ics_text = _decode_part(raw_msg)
            if ics_text:
                parsed_ics = parse_icalendar(ics_text)
                if parsed_ics:
                    ics_attachments.append({
                        "index": 0,
                        "filename": "invite.ics",
                        "ical_text": ics_text,
                        "parsed": parsed_ics,
                    })
    seen_uids = set()
    unique_ics = []
    for ics in ics_attachments:
        uid = (ics.get("parsed") or {}).get("uid")
        if uid and uid in seen_uids:
            continue
        if uid:
            seen_uids.add(uid)
        unique_ics.append(ics)
    ics_attachments = unique_ics
    client.logout()
    cached_body = message["snippet"] or ""
    subject = message["subject"]
    text_plain, text_html = _extract_message_bodies(raw_msg)
    if text_html:
        sanitized_body = sanitize_html(text_html, allow_images=allow_images)
        sanitized_body = _rewrite_cid_urls(sanitized_body, cid_map, account.id, message_id)
        sanitized_body = strip_subject_from_html(sanitized_body, subject)
    else:
        cleaned_text = strip_subject_from_text(text_plain or cached_body, subject)
        sanitized_body = plain_text_to_html(cleaned_text, cleaned=True)
    if collapse_quotes:
        sanitized_body = add_quoted_collapse(sanitized_body)
    wrapped_body = wrap_email_html(sanitized_body)
    return message, wrapped_body, attachments, flags, (text_plain, text_html), ics_attachments, cc_display


def _format_ics_dates(ics_attachments, settings_timezone):
    from app.shared.timezone import resolve_tzinfo
    user_tz = resolve_tzinfo(settings_timezone)
    for ics in ics_attachments:
        parsed = ics.get("parsed") or {}
        dtstart_str = parsed.get("dtstart")
        dtend_str = parsed.get("dtend")
        all_day = parsed.get("all_day", False)
        event_tzid = parsed.get("timezone")
        if all_day:
            ics["formatted_date"] = _format_allday_range(dtstart_str, dtend_str)
        else:
            ics["formatted_date"] = _format_timed_range(
                dtstart_str, dtend_str, event_tzid, user_tz
            )


def _parse_ics_dt(dt_str, fallback_tzid=None):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None and fallback_tzid:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(fallback_tzid))
        except Exception:
            dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_allday_range(dtstart_str, dtend_str):
    from datetime import date as date_type
    try:
        start = date_type.fromisoformat(dtstart_str[:10])
    except (ValueError, TypeError):
        return dtstart_str or ""
    end = None
    if dtend_str:
        try:
            end = date_type.fromisoformat(dtend_str[:10])
        except (ValueError, TypeError):
            pass
    if end and end != start:
        return f"{start.strftime('%a %d %b %Y')} – {end.strftime('%a %d %b %Y')}"
    return start.strftime("%a %d %b %Y")


def _format_timed_range(dtstart_str, dtend_str, event_tzid, user_tz):
    start = _parse_ics_dt(dtstart_str, event_tzid)
    if not start:
        return dtstart_str or ""
    start_local = start.astimezone(user_tz)
    end = _parse_ics_dt(dtend_str, event_tzid)
    end_local = end.astimezone(user_tz) if end else None
    tz_abbr = start_local.strftime("%Z")
    if end_local:
        if start_local.date() == end_local.date():
            return f"{start_local.strftime('%a %d %b %Y, %I:%M %p')} – {end_local.strftime('%I:%M %p')} {tz_abbr}"
        return f"{start_local.strftime('%a %d %b %Y, %I:%M %p')} – {end_local.strftime('%a %d %b %Y, %I:%M %p')} {tz_abbr}"
    return f"{start_local.strftime('%a %d %b %Y, %I:%M %p')} {tz_abbr}"


def _snippet_debug_enabled():
    return current_app.config.get("SNIPPET_DEBUG") and request.args.get("snippet_debug") == "1"


def _spam_destination(client):
    folders = list_folders(client)
    actual_by_lower = {name.lower(): name for name in folders}
    if "junk" in actual_by_lower:
        return actual_by_lower["junk"]
    if "spam" in actual_by_lower:
        return actual_by_lower["spam"]
    return None


def _build_quote_html(from_addr, to_addr, cc_addr, date_str, text_plain, text_html):
    header_lines = []
    if from_addr:
        header_lines.append(f"From: {from_addr}")
    if date_str:
        header_lines.append(f"Date: {date_str}")
    if to_addr:
        header_lines.append(f"To: {to_addr}")
    if cc_addr:
        header_lines.append(f"Cc: {cc_addr}")
    header_html = "<br>".join(header_lines)
    if text_plain:
        escaped = text_plain.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = "<br>".join(escaped.split("\n"))
    elif text_html:
        body = text_html
    else:
        body = ""
    if from_addr and date_str:
        summary = f"On {date_str}, {from_addr} wrote:"
    elif from_addr:
        summary = f"{from_addr} wrote:"
    else:
        summary = "Quoted text"
    return (
        '<div data-quote-block style="margin-top:12px;padding-left:12px;border-left:3px solid #cbd5e1;color:#64748b;">'
        '<div data-quote-summary style="display:none;font-size:12px;margin-bottom:4px;">' + summary + '</div>'
        '<div data-quote-header>' + header_html + ("<br><br>" if body else "") + '</div>'
        + (body if body else "")
        + "</div>"
    )


def _build_reply_forward_prefill(account, message_id, reply_all=False, forward=False):
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    msg = get_message(conn, message_id)
    if not msg:
        return None
    uid = msg["uid"]
    folder = msg["folder"]
    subject = msg["subject"] or ""
    sender = msg["sender"] or ""
    recipients = msg["recipients"] or ""
    date_str = msg["date"] or ""
    text_plain = ""
    text_html = ""
    cc_header = ""
    try:
        secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
        client, _domain = _imap_for_account(account, secret)
        select_folder(client, folder)
        raw_msg = fetch_message(client, uid)
        safe_logout(client)
        if raw_msg:
            cc_header = raw_msg.get("Cc", "") or ""
            text_plain, text_html = _extract_message_bodies(raw_msg)
    except Exception:
        logger.warning("reply/forward prefill IMAP failed account_id=%s message_id=%s", account.id, message_id, exc_info=True)
    if not text_plain and not text_html:
        text_plain = msg["snippet"] or ""
    cc_display = decode_address_header(cc_header) if cc_header else ""
    my_email = account.email_address.lower()
    if forward:
        fwd_subject = subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}"
        quote = _build_quote_html(sender, recipients, cc_display, date_str, text_plain, text_html)
        body_html = f"<br><br>{quote}"
        return {
            "to_addrs": "",
            "cc_addrs": "",
            "bcc_addrs": "",
            "subject": fwd_subject,
            "body_html": body_html,
        }
    re_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    cc_addrs = ""
    if reply_all:
        all_cc = []
        for _, addr in getaddresses([recipients]):
            if addr and addr.lower() != my_email:
                all_cc.append(addr)
        for _, addr in getaddresses([cc_header]):
            if addr and addr.lower() != my_email:
                all_cc.append(addr)
        cc_addrs = ", ".join(all_cc)
    quote = _build_quote_html(sender, recipients, cc_display, date_str, text_plain, text_html)
    return {
        "to_addrs": sender,
        "cc_addrs": cc_addrs,
        "bcc_addrs": "",
        "subject": re_subject,
        "body_html": f"<br><br>{quote}",
    }


def _thread_sort_ts(row):
    from app.modules.mail.services.cache_db import _date_to_unix
    sort_ts = row["sort_ts"] if "sort_ts" in row.keys() else None
    if sort_ts and isinstance(sort_ts, (int, float)) and sort_ts > 0:
        return sort_ts
    return _message_date_ts(row["date"]) or 0


def _load_thread_for_detail(conn, thread_id, current_message_id, subject, account_email=None, timezone_name=None):
    from app.modules.mail.services.cache_db import list_thread_messages

    thread_rows = list(list_thread_messages(conn, thread_id)) if thread_id else []

    current_in_thread = any(r[0] == current_message_id for r in thread_rows)
    if not current_in_thread:
        current_row = conn.execute(
            "SELECT id, uid, folder, subject, sender, recipients, date, flags, body, has_attachments, message_id, thread_id, COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject, cc, attachment_list FROM messages WHERE id = ?",
            (current_message_id,),
        ).fetchone()
        if current_row:
            thread_rows.append(current_row)

    if len(thread_rows) <= 1:
        norm_subj = normalize_subject_for_threading(subject)
        if norm_subj:
            all_rows = conn.execute(
                "SELECT id, uid, folder, subject, sender, recipients, date, flags, body, has_attachments, message_id, thread_id, COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject, cc, attachment_list FROM messages"
            ).fetchall()
            existing_ids = {row["id"] for row in thread_rows}
            for row in all_rows:
                if row["id"] in existing_ids:
                    continue
                if normalize_subject_for_threading(row["subject"]) == norm_subj:
                    thread_rows.append(row)
                    existing_ids.add(row["id"])
    else:
        norm_subj = normalize_subject_for_threading(subject)
        if norm_subj:
            draft_rows = conn.execute(
                "SELECT id, uid, folder, subject, sender, recipients, date, flags, body, has_attachments, message_id, thread_id, COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject, cc, attachment_list FROM messages WHERE LOWER(folder) = 'drafts'"
            ).fetchall()
            existing_ids = {row["id"] for row in thread_rows}
            for row in draft_rows:
                if row["id"] in existing_ids:
                    continue
                if normalize_subject_for_threading(row["subject"]) == norm_subj:
                    thread_rows.append(row)
                    existing_ids.add(row["id"])

    seen = set()
    unique = []
    for row in thread_rows:
        if row["id"] not in seen:
            seen.add(row["id"])
            unique.append(row)

    unique.sort(key=_thread_sort_ts, reverse=True)

    result = []
    for row in unique:
        folder = row["folder"] or ""
        is_sent = folder.lower() in ("sent", "sent items", "sent messages") if folder else False
        sender_full = decode_address_header(row["sender"])
        sender_display, sender_tooltip = _format_sender(sender_full)
        sender_addresses = getaddresses([sender_full])
        sender_email = sender_addresses[0][1] if sender_addresses else ""
        if not sender_email:
            angle_match = re.search(r"<([^>]+@[^>]+)>", sender_full)
            if angle_match:
                sender_email = angle_match.group(1)
        recipients_raw = row["recipients"]
        recipients_display = ""
        recipients_email = ""
        if recipients_raw:
            addrs = getaddresses([decode_address_header(recipients_raw)])
            recipients_display = ", ".join(name or addr for name, addr in addrs[:2])
            recipients_email = ", ".join(addr for _, addr in addrs if addr)

        cached_body = row["body"] or ""
        body_html = plain_text_to_html(cached_body, cleaned=True)
        body_html = add_quoted_collapse(body_html)
        wrapped_body = wrap_email_html(body_html)

        snippet = normalize_preview_text(row["body"], limit=200)
        msg_flags = _parse_flags(row["flags"])

        is_bounce = bool(row["is_bounce"])
        bounce_reason = row["bounce_reason"]
        original_subject = normalize_header_text(row["original_subject"]) if row["original_subject"] else None
        cc_raw = row["cc"]
        cc_display = decode_address_header(cc_raw) if cc_raw else ""
        display_subject = original_subject or normalize_header_text(row["subject"]) or "(no subject)"
        is_draft = "\\Draft" in msg_flags or (folder and folder.lower() == "drafts")
        attachment_list_raw = row["attachment_list"]
        attachment_names = json.loads(attachment_list_raw) if attachment_list_raw else []

        result.append({
            "id": row["id"],
            "uid": row["uid"],
            "folder": folder,
            "subject": display_subject,
            "sender": sender_full,
            "sender_display": sender_display,
            "sender_tooltip": sender_tooltip or sender_full,
            "sender_email": sender_email,
            "recipients": decode_address_header(row["recipients"]),
            "recipients_display": recipients_display,
            "recipients_email": recipients_email,
            "date": row["date"],
            "date_display": _format_short_date(row["date"], timezone_name),
            "date_ts": _message_date_ts(row["date"]),
            "flags": msg_flags,
            "is_unread": "\\Seen" not in msg_flags,
            "is_flagged": "\\Flagged" in msg_flags,
            "is_sent": is_sent,
            "is_draft": is_draft,
            "is_current": row["id"] == current_message_id,
            "snippet": snippet,
            "body_html": wrapped_body,
            "has_attachments": bool(row["has_attachments"]),
            "attachment_names": attachment_names,
            "is_bounce": is_bounce,
            "bounce_reason": bounce_reason,
            "cc": cc_display,
        })

    return result
