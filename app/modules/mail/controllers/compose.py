import logging
import re
import uuid
import time

from flask import session, request, redirect, url_for, render_template, jsonify, current_app

from app.shared.db import db
from app.shared.models.core import CustomerAccount, Domain
from app.shared.keys import get_user_key
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.imap_client import (
    delete_message_by_uid,
    ensure_folder_and_append,
    fetch_message,
    parse_append_uid,
    safe_logout,
    select_folder,
)
from app.modules.mail.utils.sanitize import html_to_text_lines
from app.modules.mail.services.cache_db import open_cache, delete_messages_by_uids
from app.shared.auth import require_customer

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate

from app.modules.mail.controllers.helpers import (
    mail_bp,
    _pending_sends,
    _pending_sends_lock,
    _send_failure_notice,
    _UNDO_SECONDS,
    _cleanup_pending_sends,
    _send_status_snapshot,
    _start_send_worker,
    _imap_for_account,
    _build_reply_forward_prefill,
    _extract_message_bodies,
)
from app.modules.mail.services import attachments as _staging


logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")
_ENTITY_RE = re.compile(r"&nbsp;|&#160;", re.IGNORECASE)


def _has_text_content(html):
    if not html:
        return False
    text = _ENTITY_RE.sub(" ", _TAG_RE.sub("", html))
    return len(text.strip()) > 0


_DEFAULT_MAX_TOTAL = 50 * 1024 * 1024


def _collect_staged_attachments(user_id):
    """Collect staged attachment bytes for this send/draft.

    Returns (files, over_limit) where files is a list of
    {"name", "mime", "data"} dicts. Reads only the IDs the client submitted
    in attachment_ids, validating each belongs to the user's staging tree.
    """
    compose_session_id = (request.form.get("compose_session_id") or "").strip()
    raw_ids = request.form.get("attachment_ids") or ""
    attachment_ids = [aid.strip() for aid in raw_ids.split(",") if aid.strip()]
    if not compose_session_id or not attachment_ids:
        return [], False
    if not _staging.is_valid_id(compose_session_id):
        return [], False
    try:
        max_total = int(current_app.config.get("MAIL_ATTACHMENT_MAX_TOTAL_BYTES", _DEFAULT_MAX_TOTAL))
    except (TypeError, ValueError):
        max_total = _DEFAULT_MAX_TOTAL
    collected = []
    total = 0
    for aid in attachment_ids:
        if not _staging.is_valid_id(aid):
            continue
        meta = _staging.read_meta(user_id, compose_session_id, aid)
        data = _staging.read_bytes(user_id, compose_session_id, aid)
        if meta is None or data is None:
            logger.warning(
                "staged attachment missing user_id=%s sid=%s id=%s",
                user_id, compose_session_id, aid,
            )
            continue
        total += len(data)
        if total > max_total:
            return collected, True
        collected.append({
            "name": meta.get("name", aid),
            "mime": meta.get("mime", "application/octet-stream"),
            "data": data,
        })
    return collected, False


def _delete_staging_session(user_id):
    sid = (request.form.get("compose_session_id") or "").strip()
    if _staging.is_valid_id(sid):
        _staging.delete_session(user_id, sid)


def _load_draft_prefill(account, draft_uid):
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    row = conn.execute(
        "SELECT uid, folder, flags FROM messages WHERE uid = ? AND LOWER(folder) = 'drafts'",
        (draft_uid,),
    ).fetchone()
    if not row:
        return None
    uid = row["uid"]
    folder = row["folder"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client = None
    try:
        client, _domain = _imap_for_account(account, secret)
        select_folder(client, folder)
        raw_msg = fetch_message(client, uid)
        safe_logout(client)
    except Exception:
        if client:
            safe_logout(client)
        logger.warning("draft prefill IMAP failed account_id=%s uid=%s", account.id, uid, exc_info=True)
        return None
    if not raw_msg:
        return None
    to_addrs = raw_msg.get("To", "") or ""
    cc_addrs = raw_msg.get("Cc", "") or ""
    bcc_addrs = raw_msg.get("Bcc", "") or ""
    subject = raw_msg.get("Subject", "") or ""
    text_plain, text_html = _extract_message_bodies(raw_msg)
    body_html = text_html or ""
    if not body_html and text_plain:
        from app.modules.mail.utils.sanitize import plain_text_to_html
        body_html = plain_text_to_html(text_plain, cleaned=True)
    return {
        "to_addrs": to_addrs,
        "cc_addrs": cc_addrs,
        "bcc_addrs": bcc_addrs,
        "subject": subject,
        "body_html": body_html,
        "draft_uid": draft_uid,
    }


@mail_bp.route("/mail/compose", methods=["GET"])
@require_customer
def compose():
    user_id = session.get("user_id")
    default_account_id = session.get("active_account_id") or 0
    account_id_raw = (request.args.get("account_id") or "").strip()
    account_id = int(account_id_raw) if account_id_raw.isdigit() else int(default_account_id or 0)
    prefill_token = (request.args.get("prefill_token") or "").strip()
    prefill = None
    compose_notice = None
    if prefill_token:
        with _pending_sends_lock:
            payload = _pending_sends.get(prefill_token)
            if payload and payload.get("user_id") == user_id:
                account_id = int(payload.get("account_id") or account_id)
                prefill = {
                    "to_addrs": payload.get("to_addrs") or "",
                    "cc_addrs": payload.get("cc_addrs") or "",
                    "bcc_addrs": payload.get("bcc_addrs") or "",
                    "subject": payload.get("subject") or "",
                    "body_html": payload.get("body_html") or "",
                    "request_receipt": bool(payload.get("request_receipt")),
                }
                if int(payload.get("attachments_count") or 0) > 0:
                    compose_notice = "Attachments are not restored automatically. Re-attach files before sending."
    draft_uid_param = (request.args.get("draft_uid") or "").strip()
    if not prefill and draft_uid_param:
        account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first()
        if account:
            prefill = _load_draft_prefill(account, draft_uid_param)
            if prefill:
                prefill["draft_uid"] = draft_uid_param
    if not prefill:
        reply_to_id = (request.args.get("reply_to") or "").strip()
        is_reply_all = request.args.get("reply_all") == "1"
        is_forward = request.args.get("forward") == "1"
        if reply_to_id and reply_to_id.isdigit():
            account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first()
            if account:
                prefill = _build_reply_forward_prefill(
                    account, int(reply_to_id), reply_all=is_reply_all, forward=is_forward
                )
    return render_template(
        "compose.html",
        account_id=account_id,
        prefill=prefill,
        compose_notice=compose_notice,
    )


@mail_bp.route("/mail/draft/<int:account_id>/<string:draft_uid>/discard", methods=["POST"])
@require_customer
def discard_draft(account_id, draft_uid):
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    key = get_user_key(user_id)
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client = None
    try:
        client, _domain = _imap_for_account(account, secret)
        select_folder(client, "Drafts")
        delete_message_by_uid(client, draft_uid)
    except Exception:
        logger.warning("discard draft failed account_id=%s uid=%s", account_id, draft_uid, exc_info=True)
    finally:
        if client:
            safe_logout(client)
    try:
        conn = open_cache(account.cache_db_path, key)
        delete_messages_by_uids(conn, "Drafts", [draft_uid])
    except Exception:
        logger.warning("discard draft cache cleanup failed account_id=%s uid=%s", account_id, draft_uid, exc_info=True)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok"})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder="INBOX"))


@mail_bp.route("/mail/send/<string:send_token>", methods=["GET"])
@require_customer
def send_status_page(send_token):
    _cleanup_pending_sends()
    user_id = session.get("user_id")
    with _pending_sends_lock:
        payload = _pending_sends.get(send_token)
        if not payload or payload.get("user_id") != user_id:
            return redirect(url_for("mail.mailbox"))
    return render_template("sent.html", send_token=send_token, undo_seconds=_UNDO_SECONDS)


@mail_bp.route("/mail/send/status/<string:send_token>")
@require_customer
def send_status(send_token):
    _cleanup_pending_sends()
    user_id = session.get("user_id")
    with _pending_sends_lock:
        payload = _pending_sends.get(send_token)
        if not payload or payload.get("user_id") != user_id:
            return jsonify({"status": "error", "error": "Send request not found."}), 404
        snapshot = _send_status_snapshot(send_token, payload)
    return jsonify(snapshot)


@mail_bp.route("/mail/send", methods=["POST"])
@require_customer
def send_mail():
    user_id = session.get("user_id")
    account_id = int(request.form.get("account_id") or 0)
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.is_active:
        return render_template(
            "compose.html",
            account_id=account_id,
            error="Domain is unavailable.",
            prefill={
                "to_addrs": request.form.get("to", ""),
                "cc_addrs": request.form.get("cc", ""),
                "bcc_addrs": request.form.get("bcc", ""),
                "subject": request.form.get("subject", ""),
                "body_html": request.form.get("body_html", ""),
                "request_receipt": request.form.get("read_receipt") == "on",
            },
        )
    key = get_user_key(user_id)
    if not key:
        logger.warning("send missing key user_id=%s account_id=%s", user_id, account_id)
        session.clear()
        return render_template("login.html", error="Session expired. Please log in again.")
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None

    from_addr = account.email_address
    to_addrs = request.form.get("to", "")
    cc_addrs = request.form.get("cc", "")
    bcc_addrs = request.form.get("bcc", "")
    subject = request.form.get("subject", "")
    body_html = request.form.get("body_html", "")
    body = html_to_text_lines(body_html)
    request_receipt = request.form.get("read_receipt") == "on"

    msg_root = MIMEMultipart("mixed")
    msg_root["From"] = from_addr
    msg_root["To"] = to_addrs
    if cc_addrs:
        msg_root["Cc"] = cc_addrs
    msg_root["Subject"] = subject
    msg_root["Date"] = formatdate(localtime=True)
    if request_receipt:
        msg_root["Disposition-Notification-To"] = from_addr

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body or "", "plain"))
    if body_html:
        alt.attach(MIMEText(body_html, "html"))
    msg_root.attach(alt)

    staged_files, over_limit = _collect_staged_attachments(user_id)
    if over_limit:
        return render_template(
            "compose.html",
            account_id=account_id,
            error="Total attachment size exceeds the limit. Remove some files and try again.",
            prefill={
                "to_addrs": to_addrs,
                "cc_addrs": cc_addrs,
                "bcc_addrs": bcc_addrs,
                "subject": subject,
                "body_html": body_html,
                "request_receipt": request_receipt,
            },
        )
    attachments_count = len(staged_files)
    for f in staged_files:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f["data"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=f["name"])
        msg_root.attach(part)

    msg = msg_root.as_bytes()

    sent_msg = msg
    if bcc_addrs:
        sent_root = MIMEMultipart("mixed")
        sent_root["From"] = from_addr
        sent_root["To"] = to_addrs
        if cc_addrs:
            sent_root["Cc"] = cc_addrs
        sent_root["Bcc"] = bcc_addrs
        sent_root["Subject"] = subject
        sent_root["Date"] = msg_root["Date"]
        if request_receipt:
            sent_root["Disposition-Notification-To"] = from_addr
        sent_root.attach(alt)
        for f in staged_files:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f["data"])
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=f["name"])
            sent_root.attach(part)
        sent_msg = sent_root.as_bytes()
    send_token = uuid.uuid4().hex
    now_ts = time.time()
    with _pending_sends_lock:
        _pending_sends[send_token] = {
            "user_id": user_id,
            "account_id": account_id,
            "domain_id": domain.id,
            "auth_type": account.auth_type,
            "secret": secret,
            "from_addr": from_addr,
            "to_addrs": to_addrs,
            "cc_addrs": cc_addrs,
            "bcc_addrs": bcc_addrs,
            "subject": subject,
            "body_html": body_html,
            "request_receipt": request_receipt,
            "attachments_count": attachments_count,
            "msg": msg,
            "sent_msg": sent_msg,
            "draft_uid": (request.form.get("draft_uid") or "").strip() or None,
            "status": "countdown",
            "send_after": now_ts + _UNDO_SECONDS,
            "created_at": now_ts,
            "updated_at": now_ts,
            "error": None,
            "warning": None,
        }
    _cleanup_pending_sends(now_ts=now_ts)
    _start_send_worker(send_token, delay_seconds=_UNDO_SECONDS)
    _delete_staging_session(user_id)
    session["active_send"] = {"token": send_token, "subject": subject}
    return redirect(url_for("mail.mailbox"))


@mail_bp.route("/mail/send/now", methods=["POST"])
@require_customer
def send_now():
    token = (request.form.get("token") or "").strip()
    user_id = session.get("user_id")
    with _pending_sends_lock:
        payload = _pending_sends.get(token)
        if not payload or payload.get("user_id") != user_id:
            return jsonify({"status": "error", "error": "Send request not found."}), 404
        if payload.get("status") not in ("countdown", "queued"):
            return jsonify(_send_status_snapshot(token, payload))
        payload["status"] = "queued"
        payload["send_after"] = time.time()
        payload["updated_at"] = payload["send_after"]
        snapshot = _send_status_snapshot(token, payload)
    _start_send_worker(token, delay_seconds=0)
    return jsonify(snapshot)


@mail_bp.route("/mail/send/retry", methods=["POST"])
@require_customer
def retry_send():
    token = (request.form.get("token") or "").strip()
    user_id = session.get("user_id")
    wants_json = "application/json" in (request.headers.get("Accept") or "").lower()
    with _pending_sends_lock:
        payload = _pending_sends.get(token)
        if not payload or payload.get("user_id") != user_id:
            if wants_json:
                return jsonify({"status": "error", "error": "Send request not found."}), 404
            return redirect(url_for("mail.mailbox"))
        if payload.get("status") == "failed":
            payload["status"] = "queued"
            payload["send_after"] = time.time()
            payload["updated_at"] = payload["send_after"]
            payload["error"] = None
            payload["warning"] = None
            if _send_failure_notice.get(user_id) == token:
                _send_failure_notice.pop(user_id, None)
            _start_send = True
        else:
            _start_send = False
        snapshot = _send_status_snapshot(token, payload)
    if _start_send:
        _start_send_worker(token, delay_seconds=0)
    if wants_json:
        return jsonify(snapshot)
    return redirect(url_for("mail.send_status_page", send_token=token))


@mail_bp.route("/mail/undo-send", methods=["POST"])
@require_customer
def undo_send():
    token = (request.form.get("token") or "").strip()
    user_id = session.get("user_id")
    with _pending_sends_lock:
        payload = _pending_sends.get(token)
        if payload and payload.get("user_id") == user_id and payload.get("status") in ("countdown", "queued"):
            payload["status"] = "cancelled"
            payload["updated_at"] = time.time()
            payload["error"] = None
    active_send = session.get("active_send")
    if active_send and active_send.get("token") == token:
        session.pop("active_send", None)
    wants_json = request.headers.get("Accept", "").lower().startswith("application/json")
    if wants_json:
        with _pending_sends_lock:
            payload = _pending_sends.get(token)
        if payload:
            return jsonify(_send_status_snapshot(token, payload))
        return jsonify({"status": "cancelled"})
    return redirect(url_for("mail.mailbox"))


@mail_bp.route("/mail/send/dismiss", methods=["POST"])
@require_customer
def dismiss_send_notification():
    session.pop("active_send", None)
    return jsonify({"status": "ok"})


@mail_bp.route("/mail/draft/auto-save", methods=["POST"])
@require_customer
def auto_save_draft():
    user_id = session.get("user_id")
    account_id = int(request.form.get("account_id") or 0)
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first()
    if not account:
        return jsonify({"ok": False, "error": "Account not found."}), 404

    body_html = request.form.get("body_html", "")
    if not _has_text_content(body_html):
        return jsonify({"ok": True, "draft_uid": None})

    key = get_user_key(user_id)
    if not key:
        return jsonify({"ok": False, "error": "Session expired."}), 401
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None

    from_addr = account.email_address
    to_addrs = request.form.get("to", "")
    cc_addrs = request.form.get("cc", "")
    bcc_addrs = request.form.get("bcc", "")
    subject = request.form.get("subject", "")
    body = html_to_text_lines(body_html)
    old_draft_uid = (request.form.get("draft_uid") or "").strip()

    msg_root = MIMEMultipart("mixed")
    msg_root["From"] = from_addr
    msg_root["To"] = to_addrs
    if cc_addrs:
        msg_root["Cc"] = cc_addrs
    if bcc_addrs:
        msg_root["Bcc"] = bcc_addrs
    msg_root["Subject"] = subject
    msg_root["Date"] = formatdate(localtime=True)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body or "", "plain"))
    if body_html:
        alt.attach(MIMEText(body_html, "html"))
    msg_root.attach(alt)

    client = None
    try:
        client, _domain = _imap_for_account(account, secret)
        if old_draft_uid:
            try:
                select_folder(client, "Drafts")
                delete_message_by_uid(client, old_draft_uid)
            except Exception:
                logger.debug("failed to delete old draft uid=%s", old_draft_uid, exc_info=True)
            try:
                conn = open_cache(account.cache_db_path, key)
                delete_messages_by_uids(conn, "Drafts", [old_draft_uid])
            except Exception:
                logger.debug("failed to delete old draft from cache uid=%s", old_draft_uid, exc_info=True)

        status, data = ensure_folder_and_append(client, "Drafts", msg_root.as_bytes(), flags=["\\Draft"])
        if status != "OK":
            raise RuntimeError("Unable to save draft to Drafts folder.")
        new_uid = parse_append_uid(data)
        return jsonify({"ok": True, "draft_uid": new_uid})
    except Exception:
        logger.exception("auto-save draft failed account_id=%s customer_id=%s", account.id, user_id)
        return jsonify({"ok": False, "error": "Unable to save draft right now."}), 500
    finally:
        if client:
            safe_logout(client)


@mail_bp.route("/mail/draft", methods=["POST"])
@require_customer
def save_draft():
    user_id = session.get("user_id")
    account_id = int(request.form.get("account_id") or 0)
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    key = get_user_key(user_id)
    if not key:
        logger.warning("save draft missing key user_id=%s account_id=%s", user_id, account_id)
        session.clear()
        return render_template("login.html", error="Session expired. Please log in again.")
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None

    from_addr = account.email_address
    to_addrs = request.form.get("to", "")
    cc_addrs = request.form.get("cc", "")
    bcc_addrs = request.form.get("bcc", "")
    subject = request.form.get("subject", "")
    body_html = request.form.get("body_html", "")
    body = html_to_text_lines(body_html)
    old_draft_uid = (request.form.get("draft_uid") or "").strip()

    msg_root = MIMEMultipart("mixed")
    msg_root["From"] = from_addr
    msg_root["To"] = to_addrs
    if cc_addrs:
        msg_root["Cc"] = cc_addrs
    if bcc_addrs:
        msg_root["Bcc"] = bcc_addrs
    msg_root["Subject"] = subject
    msg_root["Date"] = formatdate(localtime=True)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body or "", "plain"))
    if body_html:
        alt.attach(MIMEText(body_html, "html"))
    msg_root.attach(alt)

    staged_files, over_limit = _collect_staged_attachments(user_id)
    if over_limit:
        return render_template(
            "compose.html",
            account_id=account_id,
            error="Total attachment size exceeds the limit. Remove some files and try again.",
            prefill={
                "to_addrs": to_addrs,
                "cc_addrs": cc_addrs,
                "bcc_addrs": bcc_addrs,
                "subject": subject,
                "body_html": body_html,
                "request_receipt": request.form.get("read_receipt") == "on",
            },
        )
    for f in staged_files:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f["data"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=f["name"])
        msg_root.attach(part)

    client = None
    try:
        client, _domain = _imap_for_account(account, secret)
        if old_draft_uid:
            try:
                select_folder(client, "Drafts")
                delete_message_by_uid(client, old_draft_uid)
            except Exception:
                logger.debug("failed to delete old draft uid=%s", old_draft_uid, exc_info=True)
            try:
                conn = open_cache(account.cache_db_path, key)
                delete_messages_by_uids(conn, "Drafts", [old_draft_uid])
            except Exception:
                logger.debug("failed to delete old draft from cache uid=%s", old_draft_uid, exc_info=True)
        status, _data = ensure_folder_and_append(client, "Drafts", msg_root.as_bytes(), flags=["\\Draft"])
        if status != "OK":
            raise RuntimeError("Unable to save draft to Drafts folder.")
    except Exception:
        logger.exception("save draft failed account_id=%s customer_id=%s", account.id, session.get("user_id"))
        return render_template(
            "compose.html",
            account_id=account_id,
            prefill={
                "to_addrs": to_addrs,
                "cc_addrs": cc_addrs,
                "bcc_addrs": bcc_addrs,
                "subject": subject,
                "body_html": body_html,
                "request_receipt": request.form.get("read_receipt") == "on",
            },
            error="Unable to save draft right now. Retry, check connection, or refresh.",
        )
    finally:
        if client:
            safe_logout(client)
    _delete_staging_session(user_id)
    return redirect(url_for("mail.mailbox"))
