from flask import session, request, redirect, url_for, render_template

from app.shared.models.core import CustomerAccount
from app.shared.keys import get_user_key
from app.modules.mail.services.cache_db import open_cache
from app.shared.auth import require_customer

from app.modules.mail.controllers.helpers import (
    mail_bp,
    _get_or_create_settings,
    _folder_sidebar_context,
    _consume_send_failure_notice,
    _current_undo_action,
    _spam_action_enabled,
    _decorate_message_row,
    normalize_subject_for_threading,
)


@mail_bp.route("/mail/tags", methods=["POST"])
@require_customer
def create_tag():
    name = request.form.get("name", "").strip()
    account_id = int(request.form.get("account_id"))
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    from app.modules.mail.services.cache_db import create_tag as _create_tag
    _create_tag(conn, name)
    return redirect(url_for("mail.folder_view", account_id=account_id, folder="Inbox"))


@mail_bp.route("/mail/message/<int:account_id>/<int:message_id>/tag", methods=["POST"])
@require_customer
def tag_message_route(account_id, message_id):
    tag_id = int(request.form.get("tag_id"))
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    from app.modules.mail.services.cache_db import tag_message
    tag_message(conn, message_id, tag_id)
    return redirect(url_for("mail.message_view", account_id=account_id, message_id=message_id))


@mail_bp.route("/mail/tag/<int:account_id>/<int:tag_id>")
@require_customer
def tag_view(account_id, tag_id):
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    key = get_user_key(user_id)
    conn = open_cache(account.cache_db_path, key)
    from app.modules.mail.services.cache_db import list_messages_by_tag
    settings = _get_or_create_settings(user_id)
    messages = list_messages_by_tag(conn, tag_id)
    threads = {}
    for msg in messages:
        row = _decorate_message_row(msg, timezone_name=settings.timezone)
        thread_key = normalize_subject_for_threading(row["subject"])
        threads.setdefault(thread_key, []).append(row)
    from app.modules.mail.services.cache_db import has_completed_sync
    accounts, folder_sections, cached_folders, pinned, starred_count, sidebar_warning = _folder_sidebar_context(
        user_id, account, key, conn
    )
    send_failure = _consume_send_failure_notice(user_id)
    return render_template(
        "folder.html",
        account=account,
        accounts=accounts,
        folder=f"Tag {tag_id}",
        active_folder_key=f"TAG {tag_id}".upper(),
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
