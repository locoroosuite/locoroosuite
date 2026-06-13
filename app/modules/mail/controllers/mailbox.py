import json
import logging
from pathlib import Path

from flask import session, request, redirect, url_for, render_template, current_app, jsonify

from app.shared.db import db
from app.shared.models.core import CustomerAccount, CustomerSettings
from app.shared.keys import get_user_key
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.imap_client import select_folder, create_folder
from app.modules.mail.services.cache import purge_cache
from app.modules.mail.services.cache_db import open_cache
from app.shared.auth import require_customer

from app.modules.mail.controllers.helpers import (
    mail_bp,
    _get_or_create_settings,
    _build_threads,
    _folder_sidebar_context,
    _snippet_debug_enabled,
    _consume_send_failure_notice,
    _current_undo_action,
    _spam_action_enabled,
    _imap_for_account,
    _decorate_message_row,
    normalize_subject_for_threading,
)


logger = logging.getLogger(__name__)


@mail_bp.route("/mail/")
@require_customer
def mailbox():
    user_id = session.get("user_id")
    accounts = CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).all()
    logger.info("mailbox view user_id=%s accounts=%s", user_id, len(accounts))
    if not accounts:
        return render_template("mailbox.html", accounts=accounts)
    active_id = session.get("active_account_id") or accounts[0].id
    if active_id not in [acct.id for acct in accounts]:
        active_id = accounts[0].id
    session["active_account_id"] = active_id
    current_app.sync_manager.set_active_account(user_id, active_id)
    current_app.sync_manager.set_active_folder(active_id, "INBOX")
    return redirect(url_for("mail.folder_view", account_id=active_id, folder="INBOX"))


@mail_bp.route("/mail/folder/<int:account_id>/<path:folder>")
@require_customer
def folder_view(account_id, folder):
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    key = get_user_key(user_id)
    if not key:
        logger.warning("folder view missing key user_id=%s account_id=%s", user_id, account.id)
        session.clear()
        return render_template("login.html", error="Session expired. Please log in again.")
    session["active_account_id"] = account.id
    current_app.sync_manager.set_active_account(user_id, account.id)
    current_app.sync_manager.set_active_folder(account.id, folder)
    initial_syncing = current_app.sync_manager.enqueue_sync(account.id, folder=folder, reason="folder_open", priority=0)
    conn = open_cache(account.cache_db_path, key)
    settings = _get_or_create_settings(user_id)
    page = request.args.get("page", 1, type=int)
    threads, pagination = _build_threads(conn, folder, timezone_name=settings.timezone, account_email=account.email_address, page=page)
    logger.info(
        "folder view account_id=%s folder=%s page=%s total_threads=%s total_messages=%s",
        account.id,
        folder,
        pagination["current_page"],
        pagination["total_threads"],
        pagination["total_messages"],
    )
    from app.modules.mail.services.cache_db import has_completed_sync
    accounts, folder_sections, cached_folders, pinned, starred_count, sidebar_warning = _folder_sidebar_context(
        user_id, account, key, conn
    )
    snippet_debug_enabled = _snippet_debug_enabled()
    send_failure = _consume_send_failure_notice(user_id)
    return render_template(
        "folder.html",
        account=account,
        accounts=accounts,
        folder=folder,
        active_folder_key=folder.upper(),
        folder_sections=folder_sections,
        threads=threads,
        cached_folders=cached_folders,
        starred_count=starred_count,
        pinned_lookup={name.lower(): True for name in (pinned or [])},
        is_smart_view=False,
        initial_syncing=initial_syncing,
        has_completed_sync=has_completed_sync(conn),
        undo_action=_current_undo_action(),
        undo_error=session.pop("undo_error", None),
        imap_sidebar_warning=sidebar_warning,
        send_failure=send_failure,
        spam_action_enabled=_spam_action_enabled(settings, account.id),
        snippet_debug=snippet_debug_enabled,
        pagination=pagination,
    )


@mail_bp.route("/mail/reset-cache/<int:account_id>", methods=["POST"])
@require_customer
def reset_cache(account_id):
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    if account.cache_db_path:
        cache_path = Path(account.cache_db_path)
        if cache_path.exists():
            cache_path.unlink()
            logger.info("cache file deleted user_id=%s account_id=%s path=%s", user_id, account.id, account.cache_db_path)
        account.cache_db_path = None
        db.session.commit()
    current_app.sync_manager.enqueue_sync(account.id, folder="INBOX", reason="cache_reset", priority=0)
    return redirect(url_for("mail.folder_view", account_id=account.id, folder="INBOX"))


@mail_bp.route("/mail/folder/<int:account_id>/<path:folder>/messages")
@require_customer
def folder_messages(account_id, folder):
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    key = get_user_key(user_id)
    if not key:
        return jsonify({"html": "", "thread_count": 0}), 401
    conn = open_cache(account.cache_db_path, key)
    settings = _get_or_create_settings(user_id)
    page = request.args.get("page", 1, type=int)
    threads, pagination = _build_threads(conn, folder, timezone_name=settings.timezone, account_email=account.email_address, page=page)
    snippet_debug_enabled = _snippet_debug_enabled()
    html = render_template(
        "message_list.html",
        account=account,
        threads=threads,
        spam_action_enabled=_spam_action_enabled(settings, account.id),
        snippet_debug=snippet_debug_enabled,
    )
    return jsonify({
        "html": html,
        "thread_count": pagination["total_threads"],
        "total_threads": pagination["total_threads"],
        "total_messages": pagination["total_messages"],
        "current_page": pagination["current_page"],
        "total_pages": pagination["total_pages"],
    })


@mail_bp.route("/mail/folder/<int:account_id>/<path:folder>/mark-all-read", methods=["POST"])
@require_customer
def mark_all_read(account_id, folder):
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    secret = decrypt_with_key(account.encrypted_secret, get_user_key(session.get("user_id"))) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    select_folder(client, folder)
    client.store("1:*", "+FLAGS", "(\\Seen)")
    client.logout()
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/folder/<int:account_id>/create", methods=["POST"])
@require_customer
def create_folder_route(account_id):
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("mail.folder_view", account_id=account_id, folder="INBOX"))
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    secret = decrypt_with_key(account.encrypted_secret, get_user_key(session.get("user_id"))) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    create_folder(client, name)
    client.logout()
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=name))


@mail_bp.route("/mail/folder/<int:account_id>/<path:folder>/pin", methods=["POST"])
@require_customer
def toggle_pin_folder(account_id, folder):
    settings = CustomerSettings.query.filter_by(customer_id=session.get("user_id")).first()
    if not settings:
        settings = CustomerSettings(customer_id=session.get("user_id"))
        db.session.add(settings)
        db.session.commit()
    pinned = []
    if settings.pinned_folders:
        pinned = json.loads(settings.pinned_folders)
    if folder in pinned:
        pinned.remove(folder)
    else:
        pinned.append(folder)
    settings.pinned_folders = json.dumps(pinned)
    db.session.commit()
    return redirect(url_for("mail.folder_view", account_id=account_id, folder=folder))


@mail_bp.route("/mail/accounts/<int:account_id>/remove", methods=["POST"])
@require_customer
def remove_account(account_id):
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    purge_cache(account.cache_db_path)
    db.session.delete(account)
    db.session.commit()
    if session.get("active_account_id") == account_id:
        session.pop("active_account_id", None)
    return redirect(url_for("mail.mailbox"))


@mail_bp.route("/mail/accounts/active", methods=["POST"])
@require_customer
def set_active_account():
    user_id = session.get("user_id")
    account_id = int(request.form.get("account_id"))
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id, is_active=True).first_or_404()
    session["active_account_id"] = account.id
    current_app.sync_manager.set_active_account(user_id, account.id)
    current_app.sync_manager.set_active_folder(account.id, "INBOX")
    current_app.sync_manager.enqueue_sync(account.id, folder="INBOX", reason="account_switch", priority=0)
    next_url = request.form.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("mail.folder_view", account_id=account.id, folder="INBOX"))


@mail_bp.route("/mail/smart/<int:account_id>/<string:view>")
@require_customer
def smart_folder(account_id, view):
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    key = get_user_key(user_id)
    if not key:
        session.clear()
        return redirect(url_for("mail.login"))
    conn = open_cache(account.cache_db_path, key)
    settings = _get_or_create_settings(user_id)
    from app.modules.mail.services.cache_db import list_unread, list_flagged, list_with_attachments, has_completed_sync
    if view == "unread":
        messages = list_unread(conn)
    elif view == "starred":
        messages = list_flagged(conn)
    else:
        messages = list_with_attachments(conn)
    threads = {}
    for msg in messages:
        row = _decorate_message_row(msg, timezone_name=settings.timezone)
        thread_key = normalize_subject_for_threading(row["subject"])
        threads.setdefault(thread_key, []).append(row)
    accounts, folder_sections, cached_folders, pinned, starred_count, sidebar_warning = _folder_sidebar_context(
        user_id, account, key, conn
    )
    send_failure = _consume_send_failure_notice(user_id)
    return render_template(
        "folder.html",
        account=account,
        accounts=accounts,
        folder=view.title(),
        active_folder_key=view.upper(),
        folder_sections=folder_sections,
        threads=threads,
        cached_folders=cached_folders,
        starred_count=starred_count,
        pinned_lookup={name.lower(): True for name in (pinned or [])},
        is_smart_view=True,
        has_completed_sync=has_completed_sync(conn),
        undo_action=_current_undo_action(),
        undo_error=session.pop("undo_error", None),
        imap_sidebar_warning=sidebar_warning,
        send_failure=send_failure,
        spam_action_enabled=_spam_action_enabled(settings, account.id),
    )
