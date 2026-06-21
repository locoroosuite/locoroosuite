import logging
import time
import imaplib

from flask import session, request, redirect, url_for, render_template, Response, jsonify

from app.shared.db import db
from app.shared.models.core import CustomerAccount
from app.shared.keys import get_user_key
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.imap_client import (
    select_folder, set_flag, move_message, fetch_message,
    fetch_raw_message, create_folder, safe_logout, search_header,
)
from app.modules.mail.services.cache_db import open_cache, get_message, update_flags, list_cached_folders
from app.shared.auth import require_customer

from app.modules.mail.controllers.helpers import (
    mail_bp,
    _get_or_create_settings,
    _spam_action_enabled,
    _set_spam_action_enabled,
    _parse_flags,
    _imap_for_account,
    _is_attachment_part,
    _load_message_detail,
    _load_thread_for_detail,
    _snippet_debug_enabled,
    _spam_destination,
    _set_undo_action,
    _current_undo_action,
    _uid_to_str,
    _format_ics_dates,
    _fetch_attachment_bytes,
)
from app.modules.mail.utils.sanitize import normalize_header_text


logger = logging.getLogger(__name__)


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>")
@require_customer
def message_view(account_id, message_id):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    allow_images = request.args.get("show_images") == "1"
    message, sanitized, attachments, flags, _bodies, ics_attachments, cc_display = _load_message_detail(
        account, message_id, allow_images=allow_images, mark_seen=True, collapse_quotes=True
    )
    if not message:
        return redirect(url_for("mail.mailbox"))
    settings = _get_or_create_settings(session.get("user_id"))
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    cached_folders = list_cached_folders(conn)
    move_folders = [f[0] for f in cached_folders if f[0] != message["folder"]] if cached_folders else []
    thread_id = message["thread_id"]
    is_draft = message["folder"].lower() == "drafts" if message["folder"] else False
    if not is_draft:
        is_draft = "\\Draft" in flags
    thread_messages = _load_thread_for_detail(
        conn, thread_id, message_id, message["subject"],
        account_email=account.email_address,
        timezone_name=settings.timezone,
    )
    for tm in thread_messages:
        if tm["is_current"]:
            tm["body_html"] = sanitized
    if ics_attachments:
        _format_ics_dates(ics_attachments, settings.timezone)
    from app.shared.pandoc_formats import get_attachment_actions
    attachment_actions = {a["filename"]: get_attachment_actions(a["filename"]) for a in attachments}
    from app.modules.mail.services.protection import (
        locked_keyword_enabled,
        protection_reason,
    )
    protected_reason = protection_reason(flags, settings)
    return render_template(
        "message.html",
        message=message,
        account=account,
        body=sanitized,
        allow_images=allow_images,
        attachments=attachments,
        attachment_actions=attachment_actions,
        flags=flags,
        spam_action_enabled=_spam_action_enabled(settings, account.id),
        lock_action_enabled=locked_keyword_enabled(settings, account.id),
        protected_reason=protected_reason,
        current_folder=message["folder"],
        move_folders=move_folders,
        thread_messages=thread_messages,
        ics_attachments=ics_attachments,
        is_draft=is_draft,
        cc_display=cc_display,
    )


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/preview")
@require_customer
def message_preview(account_id, message_id):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    allow_images = request.args.get("show_images") == "1"
    message, sanitized, attachments, flags, bodies, ics_attachments, _cc_display = _load_message_detail(
        account, message_id, allow_images=allow_images, mark_seen=True
    )
    if not message:
        return Response("", status=404)
    settings = _get_or_create_settings(session.get("user_id"))
    if ics_attachments:
        _format_ics_dates(ics_attachments, settings.timezone)
    from app.shared.pandoc_formats import get_attachment_actions
    attachment_actions = {a["filename"]: get_attachment_actions(a["filename"]) for a in attachments}
    snippet_debug = None
    snippet_debug_info = None
    snippet_debug_enabled = _snippet_debug_enabled()
    if snippet_debug_enabled:
        from app.modules.mail.utils.sanitize import build_snippet_debug
        text_plain, text_html = bodies
        snippet_debug, snippet_debug_info = build_snippet_debug(text_plain, text_html, limit=500)
        info_for_log = {
            "source": snippet_debug_info.get("source"),
            "plain_len": len(text_plain or ""),
            "html_len": len(text_html or ""),
            "plain_info": snippet_debug_info.get("plain"),
            "html_info": snippet_debug_info.get("html"),
        }
        logger.info(
            "snippet debug message_id=%s account_id=%s info=%s",
            message_id,
            account.id,
            info_for_log,
        )
    return render_template(
        "message_preview.html",
        message=message,
        account=account,
        body=sanitized,
        allow_images=allow_images,
        attachments=attachments,
        attachment_actions=attachment_actions,
        flags=flags,
        snippet_debug=snippet_debug if snippet_debug_enabled else None,
        snippet_debug_info=snippet_debug_info if snippet_debug_enabled else None,
        snippet_debug_enabled=snippet_debug_enabled,
        ics_attachments=ics_attachments,
    )


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/mark", methods=["POST"])
@require_customer
def mark_message(account_id, message_id):
    action = request.form.get("action")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    message = get_message(conn, message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    was_unread = "\\Seen" not in flags
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    if action == "unread":
        set_flag(client, uid, "\\Seen", add=False)
        flags = [flag for flag in flags if flag != "\\Seen"]
    else:
        set_flag(client, uid, "\\Seen", add=True)
        if "\\Seen" not in flags:
            flags.append("\\Seen")
    update_flags(conn, message_id, flags)
    client.logout()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        is_unread = action == "unread"
        return jsonify({"status": "ok", "is_unread": is_unread, "was_unread": was_unread})
    return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/flag", methods=["POST"])
@require_customer
def flag_message(account_id, message_id):
    action = request.form.get("action", "add")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    message = get_message(conn, message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    set_flag(client, uid, "\\Flagged", add=(action != "remove"))
    client.logout()
    if action == "remove":
        flags = [flag for flag in flags if flag != "\\Flagged"]
    else:
        if "\\Flagged" not in flags:
            flags.append("\\Flagged")
    update_flags(conn, message_id, flags)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "is_flagged": action != "remove"})
    return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/lock", methods=["POST"])
@require_customer
def lock_message(account_id, message_id):
    from app.modules.mail.services.protection import LOCKED_KEYWORD
    action = request.form.get("action", "add")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    message = get_message(conn, message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    try:
        select_folder(client, folder)
        try:
            set_flag(client, uid, LOCKED_KEYWORD, add=(action != "remove"))
        except imaplib.IMAP4.error:
            safe_logout(client)
            settings = _get_or_create_settings(session.get("user_id"))
            from app.modules.mail.services.protection import set_locked_keyword_enabled
            set_locked_keyword_enabled(settings, account.id, False)
            db.session.commit()
            error_message = "Server doesn't support message lock flags, the option is disabled in your account"
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"status": "error", "error": error_message})
            session["undo_error"] = error_message
            return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))
    finally:
        safe_logout(client)
    if action == "remove":
        flags = [flag for flag in flags if flag != LOCKED_KEYWORD]
    else:
        if LOCKED_KEYWORD not in flags:
            flags.append(LOCKED_KEYWORD)
    update_flags(conn, message_id, flags)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "is_locked": action != "remove"})
    return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/move", methods=["POST"])
@require_customer
def move_message_route(account_id, message_id):
    destination = request.form.get("destination")
    if not destination:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "Destination folder is required."}), 400
        return redirect(url_for("mail.mailbox"))
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "Message not found."}), 404
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    if folder.lower() == destination.lower():
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "Message is already in that folder."}), 400
        return redirect(url_for("mail.folder_view", account_id=account_id, folder=destination))
    if destination.strip().lower() == "trash":
        from app.modules.mail.services.protection import protection_reason, protected_delete_message
        settings = _get_or_create_settings(session.get("user_id"))
        reason = protection_reason(_parse_flags(message["flags"]), settings)
        if reason:
            error_message = protected_delete_message(reason)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": error_message, "code": "PROTECTED"}), 409
            session["undo_error"] = error_message
            return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    move_message(client, uid, destination)
    client.expunge()
    client.logout()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "destination": destination})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=destination))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/delete", methods=["POST"])
@require_customer
def delete_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    settings = _get_or_create_settings(session.get("user_id"))
    from app.modules.mail.services.protection import protection_reason, protected_delete_message
    reason = protection_reason(flags, settings)
    if reason:
        error_message = protected_delete_message(reason)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message, "code": "PROTECTED"}), 409
        session["undo_error"] = error_message
        return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))
    was_unread = "\\Seen" not in flags
    message_id_header = message["message_id"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    move_message(client, uid, "Trash")
    client.expunge()
    client.logout()
    undo_action = None
    if message_id_header:
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        _set_undo_action(
            account_id,
            folder,
            "Trash",
            message_id_header,
            "Message deleted",
            action_type="delete",
            view_url=url_for("mail.folder_view", account_id=account_id, folder="Trash"),
            view_label="View Trash",
            ephemeral=True,
            shown_once=is_xhr,
        )
        undo_action = _current_undo_action(consume_ephemeral=False)
    else:
        session["undo_error"] = "Undo unavailable for this message."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "was_unread": was_unread, "undo_action": undo_action})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/archive", methods=["POST"])
@require_customer
def archive_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    was_unread = "\\Seen" not in flags
    message_id_header = message["message_id"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    create_folder(client, "Archive")
    select_folder(client, folder)
    move_message(client, uid, "Archive")
    client.expunge()
    client.logout()
    undo_action = None
    if message_id_header:
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        _set_undo_action(
            account_id,
            folder,
            "Archive",
            message_id_header,
            "Message archived",
            action_type="archive",
            view_url=url_for("mail.folder_view", account_id=account_id, folder="Archive"),
            view_label="View Archived",
            ephemeral=True,
            shown_once=is_xhr,
        )
        undo_action = _current_undo_action(consume_ephemeral=False)
    else:
        session["undo_error"] = "Undo unavailable for this message."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "was_unread": was_unread, "undo_action": undo_action})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/junk", methods=["POST"])
@require_customer
def junk_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    settings = _get_or_create_settings(session.get("user_id"))
    if not _spam_action_enabled(settings, account.id):
        error_message = "Spam action is disabled in your settings."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message})
        session["undo_error"] = error_message
        return redirect(url_for("mail.folder_view", account_id=account.id, folder=message["folder"]))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    was_unread = "\\Seen" not in flags
    message_id_header = message["message_id"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    destination = _spam_destination(client)
    if not destination:
        safe_logout(client)
        _set_spam_action_enabled(settings, account.id, False)
        db.session.commit()
        error_message = "No Spam/Junk folder available on this server. The Spam action is disabled."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message})
        session["undo_error"] = error_message
        return redirect(url_for("mail.folder_view", account_id=account.id, folder=folder))
    try:
        set_flag(client, uid, "\\Junk", add=True)
    except imaplib.IMAP4.error:
        safe_logout(client)
        _set_spam_action_enabled(settings, account.id, False)
        db.session.commit()
        error_message = "Server doesn't support Spam flags, the option is disabled in your account"
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message})
        session["undo_error"] = error_message
        return redirect(url_for("mail.folder_view", account_id=account.id, folder=folder))
    move_message(client, uid, destination)
    client.expunge()
    client.logout()
    undo_action = None
    if message_id_header:
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        view_label = f"View {destination}"
        _set_undo_action(
            account_id,
            folder,
            destination,
            message_id_header,
            "Reported as spam",
            action_type="junk",
            view_url=url_for("mail.folder_view", account_id=account_id, folder=destination),
            view_label=view_label,
            ephemeral=True,
            shown_once=is_xhr,
        )
        undo_action = _current_undo_action(consume_ephemeral=False)
    else:
        session["undo_error"] = "Undo unavailable for this message."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "was_unread": was_unread, "undo_action": undo_action})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/download")
@require_customer
def download_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    raw = fetch_raw_message(client, uid)
    client.logout()
    return Response(raw or b"", mimetype="message/rfc822", headers={"Content-Disposition": "attachment; filename=message.eml"})


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/print")
@require_customer
def print_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    cc_display = message["cc"] if message["cc"] else ""
    return render_template("print.html", message=message, cc_display=cc_display)


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/attachment/<int:index>")
@require_customer
def download_attachment(account_id, message_id, index):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    raw_msg = fetch_message(client, uid)
    client.logout()
    if not raw_msg:
        return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))
    attachments = []
    for part in raw_msg.walk():
        if _is_attachment_part(part):
            attachments.append(part)
    if index >= len(attachments):
        return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))
    part = attachments[index]
    filename = normalize_header_text(part.get_filename()) or f"attachment-{index}"
    data = part.get_payload(decode=True)
    disposition = "inline" if request.args.get("inline") == "1" else "attachment"
    return Response(data, mimetype=part.get_content_type() or "application/octet-stream", headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/attachment/<int:index>/view")
@require_customer
def view_attachment(account_id, message_id, index):
    from app.shared.pandoc_formats import get_attachment_actions, convert_to_html

    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    filename, data, content_type = _fetch_attachment_bytes(account, message_id, index)
    if not filename or not data:
        return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))
    actions = get_attachment_actions(filename)
    if not actions["view"]:
        return redirect(url_for("mail.download_attachment", account_id=account_id, message_id=message_id, index=index))
    if actions.get("native_view"):
        return Response(data, mimetype=content_type or "application/octet-stream", headers={"Content-Disposition": f'inline; filename="{filename}"'})
    pandoc_reader = actions.get("pandoc_reader")
    if not pandoc_reader:
        return redirect(url_for("mail.download_attachment", account_id=account_id, message_id=message_id, index=index))
    html_content = convert_to_html(data, pandoc_reader)
    if not html_content:
        return redirect(url_for("mail.download_attachment", account_id=account_id, message_id=message_id, index=index))
    return render_template(
        "attachment_view.html",
        filename=filename,
        html_content=html_content,
        account=account,
        message_id=message_id,
        attachment_index=index,
    )


@mail_bp.route("/mail/message/undo", methods=["POST"])
@require_customer
def undo_message_action():
    token = request.form.get("token")
    action = session.get("undo_action")
    if not action or action.get("token") != token:
        return redirect(url_for("mail.mailbox"))
    expires_at = action.get("expires_at")
    if expires_at and expires_at < time.time():
        session.pop("undo_action", None)
        session["undo_error"] = "Undo period expired."
        return redirect(url_for("mail.folder_view", account_id=action.get("account_id"), folder=action.get("source_folder")))
    message_id_header = action.get("message_id")
    if not message_id_header:
        session.pop("undo_action", None)
        session["undo_error"] = "Undo unavailable for this message."
        return redirect(url_for("mail.folder_view", account_id=action.get("account_id"), folder=action.get("source_folder")))
    account = CustomerAccount.query.filter_by(id=action.get("account_id"), customer_id=session.get("user_id")).first_or_404()
    secret = decrypt_with_key(account.encrypted_secret, get_user_key(session.get("user_id"))) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, action.get("destination_folder"))
    uids = search_header(client, "Message-ID", message_id_header)
    if not uids:
        client.logout()
        session.pop("undo_action", None)
        session["undo_error"] = "Undo failed; message not found."
        return redirect(url_for("mail.folder_view", account_id=account.id, folder=action.get("source_folder")))
    uid = _uid_to_str(uids[-1])
    move_message(client, uid, action.get("source_folder"))
    client.expunge()
    client.logout()
    session.pop("undo_action", None)
    return redirect(url_for("mail.folder_view", account_id=account.id, folder=action.get("source_folder")))
