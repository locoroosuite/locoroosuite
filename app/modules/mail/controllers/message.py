import imaplib
import logging
import time

from flask import Response, jsonify, redirect, render_template, request, session, url_for
from flask_babel import _

from app.modules.mail.controllers.attachment_serving import (
    attachment_actions_map,
    attachment_notice,
    attachment_unavailable,
    normalized_content_type,
)
from app.modules.mail.controllers.helpers import (
    _current_undo_action,
    _fetch_attachment_bytes,
    _format_ics_dates,
    _get_or_create_settings,
    _imap_for_account,
    _is_attachment_part,
    _load_message_detail,
    _load_thread_for_detail,
    _parse_flags,
    _set_undo_action,
    _snippet_debug_enabled,
    _uid_to_str,
    mail_bp,
)
from app.modules.mail.services.cache_db import (
    get_message,
    open_cache,
    update_flags,
)
from app.modules.mail.services.folder_aliases import folder_display_names
from app.modules.mail.services.folder_sort import move_destination_folders
from app.modules.mail.services.imap_client import (
    create_folder,
    fetch_message,
    fetch_message_id_header,
    fetch_raw_message,
    move_message,
    safe_logout,
    search_header,
    select_folder,
    set_flag,
)
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.spam import (
    SpamFlagUnsupportedError,
    SpamFolderMissingError,
    SpamTransientError,
    is_junk_folder,
    not_spam,
    report_spam,
)
from app.modules.mail.services.spam import set_spam_auto_disabled as _set_spam_auto_disabled
from app.modules.mail.services.spam import spam_action_enabled as _spam_action_enabled
from app.modules.mail.utils.sanitize import normalize_header_text
from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.display import text_scale_factor
from app.shared.keys import get_user_key
from app.shared.models.core import CustomerAccount

logger = logging.getLogger(__name__)


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>")
@require_customer
def message_view(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    allow_images = request.args.get("show_images") == "1"
    settings = _get_or_create_settings(session.get("user_id"))
    message, sanitized, attachments, flags, _bodies, ics_attachments, cc_display = (
        _load_message_detail(
            account,
            message_id,
            allow_images=allow_images,
            mark_seen=True,
            collapse_quotes=True,
            text_scale=text_scale_factor(settings.text_scale),
        )
    )
    if not message:
        return redirect(url_for("mail.mailbox"))
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    # U5.5a: shared move-destination mechanism (folders table, custom-first
    # ordering); labels disambiguate canonical aliases per U4.15b.
    ordered_folders = move_destination_folders(conn)
    folder_display = folder_display_names(ordered_folders)
    move_folders = [f for f in ordered_folders if f != message["folder"]]
    thread_id = message["thread_id"]
    is_draft = message["folder"].lower() == "drafts" if message["folder"] else False
    if not is_draft:
        is_draft = "\\Draft" in flags
    thread_messages = _load_thread_for_detail(
        conn,
        thread_id,
        message_id,
        message["subject"],
        account_email=account.email_address,
        timezone_name=settings.timezone,
        text_scale=text_scale_factor(settings.text_scale),
    )
    for tm in thread_messages:
        if tm["is_current"]:
            tm["body_html"] = sanitized
    if ics_attachments:
        _format_ics_dates(ics_attachments, settings.timezone)
    thread_attachment_names = [
        name for tm in thread_messages for name in (tm.get("attachment_names") or [])
    ]
    attachment_actions = attachment_actions_map(attachments, thread_attachment_names)
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
        in_junk_folder=is_junk_folder(message["folder"]),
        lock_action_enabled=locked_keyword_enabled(settings, account.id),
        protected_reason=protected_reason,
        current_folder=message["folder"],
        move_folders=move_folders,
        folder_display=folder_display,
        thread_messages=thread_messages,
        ics_attachments=ics_attachments,
        is_draft=is_draft,
        cc_display=cc_display,
    )


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/preview")
@require_customer
def message_preview(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    allow_images = request.args.get("show_images") == "1"
    settings = _get_or_create_settings(session.get("user_id"))
    message, sanitized, attachments, flags, bodies, ics_attachments, _cc_display = (
        _load_message_detail(
            account,
            message_id,
            allow_images=allow_images,
            mark_seen=True,
            text_scale=text_scale_factor(settings.text_scale),
        )
    )
    if not message:
        return Response("", status=404)
    if ics_attachments:
        _format_ics_dates(ics_attachments, settings.timezone)
    attachment_actions = attachment_actions_map(attachments)
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


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/thread-fragment")
@require_customer
def message_thread_fragment(account_id, message_id):
    """U4.7f: live-update fragment for the conversation view.

    Returns JSON with only the thread messages the page does not already
    have (``have`` = comma-separated message ids), rendered from the same
    partial as the page, plus the full id list and per-message bodies.
    Cache-only: never touches IMAP and never marks messages seen.
    """
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    if not key:
        return jsonify({"error": {"code": "AUTH_REQUIRED", "message": "Session expired."}}), 401
    conn = open_cache(account.cache_db_path, key)
    row = get_message(conn, message_id)
    if not row:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Message not found."}}), 404
    have = {part.strip() for part in (request.args.get("have") or "").split(",") if part.strip()}
    settings = _get_or_create_settings(session.get("user_id"))
    thread_messages = _load_thread_for_detail(
        conn,
        row["thread_id"],
        message_id,
        row["subject"],
        account_email=account.email_address,
        timezone_name=settings.timezone,
        text_scale=text_scale_factor(settings.text_scale),
    )
    new_messages = [tm for tm in thread_messages if str(tm["id"]) not in have]
    html = render_template(
        "_thread_stack.html",
        thread_messages=new_messages,
        account=account,
        message=row,
        attachments=[],
        attachment_actions={},
        ics_attachments=[],
        allow_images=False,
        is_draft=False,
    )
    return jsonify(
        {
            "html": html,
            "new_ids": [tm["id"] for tm in new_messages],
            "ids": [tm["id"] for tm in thread_messages],
            "bodies": {str(tm["id"]): tm["body_html"] for tm in new_messages},
            "count": len(thread_messages),
        }
    )


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/mark", methods=["POST"])
@require_customer
def mark_message(account_id, message_id):
    action = request.form.get("action")
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
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
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
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
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
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
            error_message = _(
                "Server doesn't support message lock flags, the option is disabled in your account"
            )
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"status": "error", "error": error_message})
            session["undo_error"] = error_message
            return redirect(
                url_for("mail.message_view", account_id=account_id, message_id=message_id)
            )
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
            return jsonify({"error": _("Destination folder is required.")}), 400
        return redirect(url_for("mail.mailbox"))
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": _("Message not found.")}), 404
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    if folder.lower() == destination.lower():
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": _("Message is already in that folder.")}), 400
        return redirect(url_for("mail.folder_view", account_id=account_id, folder=destination))
    if destination.strip().lower() == "trash":
        from app.modules.mail.services.protection import protected_delete_message, protection_reason

        settings = _get_or_create_settings(session.get("user_id"))
        reason = protection_reason(_parse_flags(message["flags"]), settings)
        if reason:
            error_message = protected_delete_message(reason)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": error_message, "code": "PROTECTED"}), 409
            session["undo_error"] = error_message
            return redirect(
                url_for("mail.message_view", account_id=account_id, message_id=message_id)
            )
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    move_message(client, uid, destination)
    client.expunge()
    client.logout()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "destination": destination})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=destination))


def _undo_message_id_header(client, message):
    """Message-ID for the undo token, tolerating legacy cache rows.

    Undo restores by searching IMAP for the Message-ID header. Cache rows
    written before outgoing messages always carried one (stamped in
    compose.send_mail) can hold NULL; fetch the header live before the
    message is moved so undo still works for them. Call after
    select_folder() on the message's folder."""
    header = message["message_id"]
    if header:
        return header
    try:
        return fetch_message_id_header(client, message["uid"])
    except Exception:
        logger.warning(
            "undo message-id fetch failed message_id=%s uid=%s",
            message["id"],
            message["uid"],
            exc_info=True,
        )
        return None


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/delete", methods=["POST"])
@require_customer
def delete_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    settings = _get_or_create_settings(session.get("user_id"))
    from app.modules.mail.services.protection import protected_delete_message, protection_reason

    reason = protection_reason(flags, settings)
    if reason:
        error_message = protected_delete_message(reason)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message, "code": "PROTECTED"}), 409
        session["undo_error"] = error_message
        return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))
    was_unread = "\\Seen" not in flags
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    message_id_header = _undo_message_id_header(client, message)
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
            _("Message deleted"),
            action_type="delete",
            view_url=url_for("mail.folder_view", account_id=account_id, folder="Trash"),
            view_label=_("View Trash"),
            ephemeral=True,
            shown_once=is_xhr,
        )
        undo_action = _current_undo_action(consume_ephemeral=False)
    else:
        session["undo_error"] = _("Undo unavailable for this message.")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "was_unread": was_unread, "undo_action": undo_action})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/archive", methods=["POST"])
@require_customer
def archive_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    was_unread = "\\Seen" not in flags
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    create_folder(client, "Archive")
    select_folder(client, folder)
    message_id_header = _undo_message_id_header(client, message)
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
            _("Message archived"),
            action_type="archive",
            view_url=url_for("mail.folder_view", account_id=account_id, folder="Archive"),
            view_label=_("View Archived"),
            ephemeral=True,
            shown_once=is_xhr,
        )
        undo_action = _current_undo_action(consume_ephemeral=False)
    else:
        session["undo_error"] = _("Undo unavailable for this message.")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "was_unread": was_unread, "undo_action": undo_action})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/junk", methods=["POST"])
@require_customer
def junk_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    settings = _get_or_create_settings(session.get("user_id"))
    if not _spam_action_enabled(settings, account.id):
        error_message = _("Spam action is disabled in your settings.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message})
        session["undo_error"] = error_message
        return redirect(
            url_for("mail.folder_view", account_id=account.id, folder=message["folder"])
        )
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    was_unread = "\\Seen" not in flags
    message_id_header = message["message_id"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    message_id_header = _undo_message_id_header(client, message)
    try:
        destination = report_spam(client, uid)
    except SpamFolderMissingError:
        safe_logout(client)
        _set_spam_auto_disabled(settings, account.id)
        db.session.commit()
        logger.warning(
            "spam action auto-disabled account_id=%s message_id=%s: no junk folder on server",
            account.id,
            message_id,
        )
        error_message = _(
            "No Spam/Junk folder available on this server. The Spam action is disabled."
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message})
        session["undo_error"] = error_message
        return redirect(url_for("mail.folder_view", account_id=account.id, folder=folder))
    except SpamFlagUnsupportedError:
        safe_logout(client)
        _set_spam_auto_disabled(settings, account.id)
        db.session.commit()
        logger.warning(
            "spam action auto-disabled account_id=%s message_id=%s: server rejected \\Junk flag",
            account.id,
            message_id,
        )
        error_message = _(
            "Server doesn't support Spam flags, the option is disabled in your account"
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message})
        session["undo_error"] = error_message
        return redirect(url_for("mail.folder_view", account_id=account.id, folder=folder))
    except SpamTransientError as exc:
        safe_logout(client)
        logger.warning(
            "spam action transient failure account_id=%s message_id=%s uid=%s: %s",
            account.id,
            message_id,
            uid,
            exc,
            exc_info=True,
        )
        error_message = _("Spam action failed. Please try again.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": error_message})
        session["undo_error"] = error_message
        return redirect(url_for("mail.folder_view", account_id=account.id, folder=folder))
    safe_logout(client)
    undo_action = None
    if message_id_header:
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        destination_display = folder_display_names([destination]).get(destination, destination)
        view_label = _("View %(destination)s", destination=destination_display)
        _set_undo_action(
            account_id,
            folder,
            destination,
            message_id_header,
            _("Reported as spam"),
            action_type="junk",
            view_url=url_for("mail.folder_view", account_id=account_id, folder=destination),
            view_label=view_label,
            ephemeral=True,
            shown_once=is_xhr,
        )
        undo_action = _current_undo_action(consume_ephemeral=False)
    else:
        session["undo_error"] = _("Undo unavailable for this message.")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "was_unread": was_unread, "undo_action": undo_action})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/not-junk", methods=["POST"])
@require_customer
def not_junk_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    uid = message["uid"]
    folder = message["folder"]
    flags = _parse_flags(message["flags"])
    was_unread = "\\Seen" not in flags
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    message_id_header = _undo_message_id_header(client, message)
    destination = not_spam(client, uid, folder)
    safe_logout(client)
    undo_action = None
    if message_id_header:
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        _set_undo_action(
            account_id,
            folder,
            destination or folder,
            message_id_header,
            _("Marked as not spam"),
            action_type="not_spam",
            view_url=(
                url_for("mail.folder_view", account_id=account_id, folder=destination)
                if destination
                else None
            ),
            view_label=_("View Inbox") if destination else None,
            ephemeral=True,
            shown_once=is_xhr,
        )
        undo_action = _current_undo_action(consume_ephemeral=False)
    else:
        session["undo_error"] = _("Undo unavailable for this message.")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "was_unread": was_unread, "undo_action": undo_action})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/download")
@require_customer
def download_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
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
    return Response(
        raw or b"",
        mimetype="message/rfc822",
        headers={"Content-Disposition": "attachment; filename=message.eml"},
    )


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/print")
@require_customer
def print_message(account_id, message_id):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        return redirect(url_for("mail.mailbox"))
    cc_display = message["cc"] if message["cc"] else ""
    return render_template("print.html", message=message, cc_display=cc_display)


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/attachment/<int:index>")
@require_customer
def download_attachment(account_id, message_id, index):
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    message = get_message(open_cache(account.cache_db_path, key), message_id)
    if not message:
        logger.warning(
            "attachment download missed cache message_id=%s account_id=%s",
            message_id,
            account_id,
        )
        return attachment_unavailable()
    uid = message["uid"]
    folder = message["folder"]
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    raw_msg = fetch_message(client, uid)
    client.logout()
    if not raw_msg:
        logger.warning(
            "attachment download IMAP fetch failed message_id=%s account_id=%s uid=%s",
            message_id,
            account_id,
            uid,
        )
        return attachment_unavailable()
    attachments = []
    for part in raw_msg.walk():
        if _is_attachment_part(part):
            attachments.append(part)
    if index >= len(attachments):
        logger.warning(
            "attachment download index out of range message_id=%s account_id=%s index=%s",
            message_id,
            account_id,
            index,
        )
        return attachment_unavailable()
    part = attachments[index]
    filename = normalize_header_text(part.get_filename()) or f"attachment-{index}"
    data = part.get_payload(decode=True)
    if not data:
        logger.warning(
            "attachment download empty payload message_id=%s account_id=%s index=%s",
            message_id,
            account_id,
            index,
        )
        return attachment_unavailable()
    disposition = "inline" if request.args.get("inline") == "1" else "attachment"
    return Response(
        data,
        mimetype=normalized_content_type(filename, part.get_content_type()),
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/attachment/<int:index>/view")
@require_customer
def view_attachment(account_id, message_id, index):
    from app.shared.pandoc_formats import convert_to_html, get_attachment_actions

    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    filename, data, content_type = _fetch_attachment_bytes(account, message_id, index)
    if not filename or not data:
        logger.warning(
            "attachment view fetch failed message_id=%s account_id=%s index=%s",
            message_id,
            account_id,
            index,
        )
        return attachment_unavailable()
    actions = get_attachment_actions(filename)
    download_url = url_for(
        "mail.download_attachment",
        account_id=account_id,
        message_id=message_id,
        index=index,
    )
    if not actions["view"]:
        return attachment_notice(_("This file type can't be previewed."), filename, download_url)
    if actions.get("native_view"):
        return Response(
            data,
            mimetype=normalized_content_type(filename, content_type),
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    if not actions.get("pandoc_reader"):
        return attachment_notice(_("This file type can't be previewed."), filename, download_url)
    html_content = convert_to_html(data, actions["pandoc_reader"])
    if not html_content:
        return attachment_notice(_("The preview could not be generated."), filename, download_url)
    if request.args.get("fragment") == "1":
        return jsonify({"ok": True, "filename": filename, "html": html_content})
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
        session["undo_error"] = _("Undo period expired.")
        return redirect(
            url_for(
                "mail.folder_view",
                account_id=action.get("account_id"),
                folder=action.get("source_folder"),
            )
        )
    message_id_header = action.get("message_id")
    if not message_id_header:
        session.pop("undo_action", None)
        session["undo_error"] = _("Undo unavailable for this message.")
        return redirect(
            url_for(
                "mail.folder_view",
                account_id=action.get("account_id"),
                folder=action.get("source_folder"),
            )
        )
    account = CustomerAccount.query.filter_by(
        id=action.get("account_id"), customer_id=session.get("user_id")
    ).first_or_404()
    secret = (
        decrypt_with_key(account.encrypted_secret, get_user_key(session.get("user_id")))
        if account.encrypted_secret
        else None
    )
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, action.get("destination_folder"))
    uids = search_header(client, "Message-ID", message_id_header)
    if not uids:
        client.logout()
        session.pop("undo_action", None)
        session["undo_error"] = _("Undo failed; message not found.")
        return redirect(
            url_for("mail.folder_view", account_id=account.id, folder=action.get("source_folder"))
        )
    uid = _uid_to_str(uids[-1])
    action_type = action.get("action_type")
    if action_type in ("junk", "not_spam"):
        add_flag = action_type == "not_spam"
        try:
            set_flag(client, uid, "\\Junk", add=add_flag)
        except imaplib.IMAP4.error:
            logger.warning(
                "undo %s: junk flag restore failed folder=%s uid=%s",
                action_type,
                action.get("destination_folder"),
                uid,
                exc_info=True,
            )
    move_message(client, uid, action.get("source_folder"))
    client.expunge()
    client.logout()
    session.pop("undo_action", None)
    return redirect(
        url_for("mail.folder_view", account_id=account.id, folder=action.get("source_folder"))
    )
