import email
import logging
from datetime import UTC, datetime, timedelta

from app.modules.mail.services.cache import build_cache_path
from app.modules.mail.services.cache_db import (
    delete_folder_state,
    delete_messages_by_folder,
    delete_messages_by_uids,
    get_folder_state,
    list_cached_folders,
    list_message_uids,
    list_uids_missing_internal_date_ts,
    open_cache,
    update_flags_bulk,
    update_internal_date_ts_for_uid,
    upsert_folder,
    upsert_folder_state,
    upsert_message,
)
from app.modules.mail.services.imap_client import (
    connect_imap,
    fetch_flags,
    fetch_message_uids,
    fetch_message_with_flags,
    folder_status,
    list_folders,
    login_imap,
    safe_logout,
    select_folder,
)
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.utils.sanitize import (
    build_snippet,
    decode_address_header,
    html_to_text_lines,
    normalize_header_text,
)
from app.shared.db import db
from app.shared.events import push_event
from app.shared.icalendar import parse_icalendar
from app.shared.keys import get_user_key
from app.shared.models.core import Domain

CACHE_DAYS = 30
CACHE_MAX = 100
logger = logging.getLogger(__name__)


def _check_imip_reply(account, msg, sender):
    try:
        if not msg or not msg.is_multipart():
            return
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type not in ("text/calendar", "application/ics"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                ical_text = payload.decode(charset, errors="ignore")
            except (LookupError, TypeError):
                ical_text = payload.decode(errors="ignore")
            if not ical_text:
                continue
            parsed = parse_icalendar(ical_text)
            if not parsed:
                continue
            method = (parsed.get("method") or "").upper()
            if method != "REPLY":
                continue
            uid = parsed.get("uid")
            if not uid:
                continue

            from app.modules.calendar.services.cache import get_cache_path
            cache_path = get_cache_path(account)
            if not cache_path:
                return

            key = get_user_key(account.customer_id)
            if not key:
                return

            from app.modules.calendar.services.cache_db import open_cache as open_cal_cache
            try:
                cal_conn = open_cal_cache(cache_path, key)
            except Exception:
                return
            if not cal_conn:
                return

            try:
                from app.modules.calendar.services.reply_processor import process_incoming_reply
                sender_email = ""
                if isinstance(sender, str):
                    sender_email = sender
                elif isinstance(sender, list) and sender:
                    first = sender[0]
                    if isinstance(first, tuple) and len(first) >= 2:
                        sender_email = first[1]
                process_incoming_reply(cal_conn, ical_text, sender_email, account=account)
            except Exception:
                logger.debug("imip reply processing failed uid=%s", uid, exc_info=True)
            finally:
                cal_conn.close()
            return
    except Exception:
        logger.debug("imip reply check failed", exc_info=True)


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


def _extract_message_texts(message):
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


def _build_snippet(text_plain, text_html):
    return build_snippet(text_plain, text_html, limit=500)


def _has_attachments(message):
    if not message.is_multipart():
        return False
    for part in message.walk():
        if _is_attachment_part(part):
            return True
    return False


def _extract_attachment_list(message):
    if not message.is_multipart():
        return []
    result = []
    for part in message.walk():
        if _is_attachment_part(part):
            filename = part.get_filename()
            result.append(normalize_header_text(filename) if filename else f"attachment-{len(result)}")
    return result


def _extract_bounce_info(msg):
    content_type = msg.get_content_type() or ""
    sender = (msg.get("From") or "").lower()
    is_dsn = "multipart/report" in content_type
    is_mailer_daemon = "mailer-daemon" in sender or "mail delivery subsystem" in sender
    if not is_dsn and not is_mailer_daemon:
        return None

    original_message_id = None
    original_in_reply_to = None
    original_references = None
    original_subject = None
    bounce_reason = None
    failed_recipient = None

    for part in msg.walk():
        ct = part.get_content_type() or ""
        if ct == "message/delivery-status":
            payload = part.get_payload()
            if isinstance(payload, list):
                for sub in payload:
                    if isinstance(sub, email.message.Message):
                        diag = sub.get("Diagnostic-Code", "")
                        if diag:
                            bounce_reason = diag.strip()
                        action = sub.get("Action", "")
                        if action and "failed" in action.lower():
                            fr = sub.get("Final-Recipient", "")
                            if fr:
                                failed_recipient = fr.strip()
            elif isinstance(payload, email.message.Message):
                diag = payload.get("Diagnostic-Code", "")
                if diag:
                    bounce_reason = diag.strip()

        if ct == "message/rfc822" or ct == "text/rfc822-headers":
            payload = part.get_payload()
            inner = None
            if isinstance(payload, list) and payload:
                inner = payload[0]
            elif isinstance(payload, email.message.Message):
                inner = payload
            if inner and isinstance(inner, email.message.Message):
                original_message_id = inner.get("Message-ID")
                original_in_reply_to = inner.get("In-Reply-To")
                original_references = inner.get("References")
                original_subject = inner.get("Subject")

    if bounce_reason and failed_recipient and not bounce_reason.startswith(failed_recipient):
        pass
    elif failed_recipient and not bounce_reason:
        bounce_reason = f"Delivery to {failed_recipient} failed"

    if not original_message_id and not original_in_reply_to and not original_references:
        return None

    return {
        "is_bounce": True,
        "original_message_id": original_message_id,
        "original_in_reply_to": original_in_reply_to,
        "original_references": original_references,
        "original_subject": original_subject,
        "bounce_reason": bounce_reason,
    }


def _resolve_folders(available, requested):
    from app.modules.mail.services.folder_aliases import resolve_folder_name

    if not requested:
        return list(available)
    requested_list = requested if isinstance(requested, (list, tuple)) else [requested]
    resolved = []
    for name in requested_list:
        resolved.append(resolve_folder_name(available, name))
    return resolved


def _prepare_message_args(msg, account=None):
    from app.modules.mail.services.cache_db import compute_thread_id
    subject = normalize_header_text(msg.get("Subject", "")) or "(no subject)"
    sender = decode_address_header(msg.get("From", ""))
    recipients = decode_address_header(msg.get("To", ""))
    cc = decode_address_header(msg.get("Cc", ""))
    date = msg.get("Date", "")
    text_plain, text_html = _extract_message_texts(msg)
    body = text_plain or html_to_text_lines(text_html)
    snippet = _build_snippet(text_plain, text_html)
    has_attach = _has_attachments(msg)
    attachment_list = _extract_attachment_list(msg) if has_attach else []
    message_id = msg.get("Message-ID")
    in_reply_to = msg.get("In-Reply-To")
    ref_list = msg.get("References")

    bounce = _extract_bounce_info(msg)
    is_bounce = False
    bounce_reason = None
    original_subject = None

    if bounce:
        is_bounce = True
        bounce_reason = bounce.get("bounce_reason")
        original_subject = normalize_header_text(bounce.get("original_subject") or "")
        orig_irt = bounce.get("original_in_reply_to")
        orig_refs = bounce.get("original_references")
        orig_mid = bounce.get("original_message_id")
        thread_id = compute_thread_id(orig_mid, orig_irt, orig_refs)
        in_reply_to = orig_irt or in_reply_to
        ref_list = orig_refs or ref_list
    else:
        thread_id = None

    return {
        "subject": subject,
        "sender": sender,
        "recipients": recipients,
        "cc": cc,
        "date": date,
        "snippet": snippet,
        "body": body,
        "body_html": text_html or "",
        "has_attachments": has_attach,
        "attachment_list": attachment_list,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "ref_list": ref_list,
        "thread_id": thread_id,
        "is_bounce": is_bounce,
        "bounce_reason": bounce_reason,
        "original_subject": original_subject,
    }


def _to_uid_str(uid):
    if isinstance(uid, bytes):
        return uid.decode()
    return str(uid)


def _chunked(items, size=50):
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def _build_uid_set(uids):
    return ",".join(str(uid) for uid in uids)


def _backfill_missing_date_ts(client, conn, folder):
    from app.modules.mail.services.cache_db import _date_to_unix

    missing_uids = list_uids_missing_internal_date_ts(conn, folder)
    if not missing_uids:
        return
    logger.info("backfill missing internal_date_ts folder=%s count=%s", folder, len(missing_uids))
    for uid in missing_uids:
        msg, flags, internal_date = fetch_message_with_flags(client, uid)
        if internal_date:
            ts = _date_to_unix(internal_date)
            if ts:
                logger.info("backfill updated uid=%s folder=%s internal_date_ts=%s", uid, folder, ts)
                update_internal_date_ts_for_uid(conn, folder, uid, ts)


def _sync_initial_folder(client, conn, folder, status_info, include_recent_page=False, page_size=CACHE_MAX, account=None):
    unread_uids = fetch_message_uids(client, "UNSEEN")
    since_date = (datetime.now(UTC) - timedelta(days=CACHE_DAYS)).strftime("%d-%b-%Y")
    recent_uids = fetch_message_uids(client, f"SINCE {since_date}")
    recent_page = []
    if include_recent_page:
        all_uids = fetch_message_uids(client, "ALL")
        if all_uids:
            recent_page = all_uids[-page_size:]
    combined = list(dict.fromkeys(recent_uids[-CACHE_MAX:] + unread_uids + recent_page))
    cached_uids = set(list_message_uids(conn, folder))
    new_added = 0
    last_new_at = None
    for uid in combined:
        uid_str = _to_uid_str(uid)
        if uid_str in cached_uids:
            continue
        msg, flags, internal_date = fetch_message_with_flags(client, uid_str)
        if not msg:
            continue
        args = _prepare_message_args(msg, account=account)
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
        )
        _check_imip_reply(account, msg, args["sender"])
        new_added += 1
        last_new_at = datetime.now(UTC).isoformat()
    unseen = status_info.get("UNSEEN")
    if unseen is None:
        unseen = len(unread_uids)
    else:
        unseen = int(unseen)
    upsert_folder(conn, folder, unseen)
    return len(combined), new_added, last_new_at


def _sync_incremental_folder(client, conn, folder, status_info, state, include_recent_page=False, page_size=CACHE_MAX, account=None):
    cached_uids = list_message_uids(conn, folder)
    cached_set = set(cached_uids)
    last_uidnext = state[2] if state else None
    new_uids = []
    if last_uidnext:
        new_uids = fetch_message_uids(client, f"UID {last_uidnext}:*")
    new_added = 0
    last_new_at = None
    total_new = len(new_uids)
    for uid in new_uids:
        uid_str = _to_uid_str(uid)
        if uid_str in cached_set:
            continue
        msg, flags, internal_date = fetch_message_with_flags(client, uid_str)
        if not msg:
            continue
        args = _prepare_message_args(msg, account=account)
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
        _check_imip_reply(account, msg, args["sender"])
        new_added += 1
        last_new_at = datetime.now(UTC).isoformat()

    if include_recent_page and len(cached_uids) < page_size:
        all_uids = fetch_message_uids(client, "ALL")
        if all_uids:
            recent_page = all_uids[-page_size:]
            extra_uids = [uid for uid in recent_page if _to_uid_str(uid) not in cached_set]
            total_new += len(extra_uids)
            for uid in extra_uids:
                uid_str = _to_uid_str(uid)
                msg, flags, internal_date = fetch_message_with_flags(client, uid_str)
                if not msg:
                    continue
                args = _prepare_message_args(msg, account=account)
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
                _check_imip_reply(account, msg, args["sender"])
                new_added += 1
                last_new_at = datetime.now(UTC).isoformat()

    if cached_uids:
        for chunk in _chunked(cached_uids, size=75):
            uid_set = _build_uid_set(chunk)
            flags_map = fetch_flags(client, uid_set)
            if flags_map:
                update_flags_bulk(conn, folder, flags_map)
            existing = set(_to_uid_str(uid) for uid in fetch_message_uids(client, f"UID {uid_set}"))
            missing = [uid for uid in chunk if _to_uid_str(uid) not in existing]
            if missing:
                delete_messages_by_uids(conn, folder, missing)

    unseen = status_info.get("UNSEEN")
    if unseen is None:
        unseen = len(fetch_message_uids(client, "UNSEEN"))
    else:
        unseen = int(unseen)
    upsert_folder(conn, folder, unseen)
    return total_new, new_added, last_new_at


def sync_account(account, folders=None, status_cb=None, include_recent_page=False):
    def emit(state, folder=None, done=None, total=None, message=None):
        if status_cb:
            status_cb(
                {
                    "state": state,
                    "folder": folder,
                    "done": done,
                    "total": total,
                    "message": message,
                }
            )

    key = get_user_key(account.customer_id)
    if not key:
        logger.warning(
            "imap sync skipped missing key account_id=%s customer_id=%s domain_id=%s",
            account.id,
            account.customer_id,
            account.domain_id,
        )
        emit("error", folder="INBOX", message="missing session key")
        return False

    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.is_active:
        logger.warning(
            "imap sync skipped inactive or missing domain account_id=%s customer_id=%s domain_id=%s",
            account.id,
            account.customer_id,
            account.domain_id,
        )
        emit("error", folder="INBOX", message="inactive domain")
        return False

    account.cache_db_path = build_cache_path(account.customer_id, account.id)
    db.session.commit()
    logger.info(
        "imap cache path account_id=%s customer_id=%s path=%s",
        account.id,
        account.customer_id,
        account.cache_db_path,
    )

    try:
        conn = open_cache(account.cache_db_path, key)
    except Exception:
        logger.exception(
            "imap cache open failed account_id=%s customer_id=%s path=%s",
            account.id,
            account.customer_id,
            account.cache_db_path,
        )
        emit("error", folder="INBOX", message="cache open failed")
        return False

    logger.info(
        "imap sync start account_id=%s customer_id=%s domain_id=%s",
        account.id,
        account.customer_id,
        account.domain_id,
    )

    try:
        client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
    except Exception:
        logger.exception(
            "imap connect failed account_id=%s domain_id=%s host=%s port=%s tls=%s",
            account.id,
            domain.id,
            domain.imap_host,
            domain.imap_port,
            domain.imap_tls,
        )
        emit("error", folder="INBOX", message="imap connect failed")
        return False
    secret = None
    if account.encrypted_secret:
        secret = decrypt_with_key(account.encrypted_secret, key)
    try:
        login_imap(
            client,
            account.username,
            password=secret,
        )
    except Exception:
        logger.exception(
            "imap login failed account_id=%s customer_id=%s auth_type=%s",
            account.id,
            account.customer_id,
            account.auth_type,
        )
        emit("error", folder="INBOX", message="imap login failed")
        try:
            client.logout()
        except Exception:
            pass
        return False

    any_error = False
    total_cached = 0
    try:
        available_folders = list_folders(client)
        if not folders:
            folders = ["INBOX"]
        folders = _resolve_folders(available_folders, folders)
        logger.info(
            "imap folders listed account_id=%s count=%s",
            account.id,
            len(available_folders),
        )
        for folder in folders:
            try:
                select_folder(client, folder)
                status_info = folder_status(client, folder)
                state = get_folder_state(conn, folder)
                uidvalidity = status_info.get("UIDVALIDITY")
                if state and uidvalidity and state[1] and str(state[1]) != str(uidvalidity):
                    delete_messages_by_folder(conn, folder)
                    delete_folder_state(conn, folder)
                    state = None
                if not state:
                    cached = list_message_uids(conn, folder)
                    if cached:
                        delete_messages_by_folder(conn, folder)
                emit("syncing", folder=folder, done=0, total=0)
            except Exception:
                logger.exception(
                    "imap folder scan failed account_id=%s folder=%s",
                    account.id,
                    folder,
                )
                emit("error", folder=folder, message="folder scan failed")
                any_error = True
                continue

            cached_in_folder = 0
            try:
                if not state:
                    total, added, last_new_at = _sync_initial_folder(
                        client,
                        conn,
                        folder,
                        status_info,
                        include_recent_page=include_recent_page,
                        account=account,
                    )
                else:
                    total, added, last_new_at = _sync_incremental_folder(
                        client,
                        conn,
                        folder,
                        status_info,
                        state,
                        include_recent_page=include_recent_page,
                        account=account,
                    )
                cached_in_folder = added
                _backfill_missing_date_ts(client, conn, folder)
                emit("syncing", folder=folder, done=cached_in_folder, total=total)
                upsert_folder_state(
                    conn,
                    folder,
                    uidvalidity=status_info.get("UIDVALIDITY"),
                    uidnext=int(status_info.get("UIDNEXT", 0)) if status_info.get("UIDNEXT") else None,
                    highestmodseq=status_info.get("HIGHESTMODSEQ"),
                    last_sync_at=datetime.now(UTC).isoformat(),
                    last_new_at=last_new_at,
                )
            except Exception:
                logger.exception(
                    "imap folder incremental sync failed account_id=%s folder=%s",
                    account.id,
                    folder,
                )
                emit("error", folder=folder, message="folder sync failed")
                any_error = True
                continue

            total_cached += cached_in_folder
            logger.info(
                "imap folder cached account_id=%s folder=%s messages=%s unread=%s",
                account.id,
                folder,
                cached_in_folder,
                status_info.get("UNSEEN"),
            )
            emit("complete", folder=folder, done=cached_in_folder, total=total)
    except Exception:
        logger.exception(
            "imap folder listing failed account_id=%s customer_id=%s",
            account.id,
            account.customer_id,
        )
        emit("error", folder="INBOX", message="folder listing failed")
        any_error = True
    finally:
        safe_logout(client)

    folder_counts = {name: count for name, count in list_cached_folders(conn)}
    push_event(account.customer_id, "counts_updated", {"account_id": account.id, "folder_counts": folder_counts})
    logger.info(
        "imap sync complete account_id=%s total_cached=%s",
        account.id,
        total_cached,
    )
    return not any_error
