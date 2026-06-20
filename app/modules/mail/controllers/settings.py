from flask import session, request, redirect, url_for, render_template, current_app

from app.shared.db import db
from app.shared.models.core import CustomerAccount
from app.shared.keys import get_user_key
from app.shared.timezone import COMMON_TIMEZONES
from app.modules.mail.services.cache import build_cache_path, purge_cache
from app.modules.mail.services.cache_db import open_cache
from app.shared.auth import require_customer

from app.modules.mail.controllers.helpers import (
    mail_bp,
    _get_or_create_settings,
    _load_spam_action_prefs,
)


@mail_bp.route("/mail/settings", methods=["GET", "POST"])
@require_customer
def settings():
    user_id = session.get("user_id")
    settings = _get_or_create_settings(user_id)
    accounts = CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).all()
    if request.method == "POST":
        settings.polling_interval = int(request.form.get("polling_interval", settings.polling_interval))
        settings.preview_pane_default = request.form.get("preview_pane_default") == "on"
        settings.sort_order = request.form.get("sort_order", settings.sort_order)
        tz_val = request.form.get("timezone", settings.timezone).strip()
        if tz_val.lower() == "browser":
            settings.timezone = "browser"
        elif tz_val in COMMON_TIMEZONES:
            settings.timezone = tz_val
        settings.theme = request.form.get("theme", settings.theme)
        settings.protect_starred = request.form.get("protect_starred") == "on"
        from app.modules.mail.controllers.helpers import _set_spam_action_enabled
        for account in accounts:
            enabled = request.form.get(f"spam_action_{account.id}") == "on"
            _set_spam_action_enabled(settings, account.id, enabled)
        from app.modules.mail.services.protection import set_locked_keyword_enabled
        for account in accounts:
            lock_enabled = request.form.get(f"locked_keyword_{account.id}") == "on"
            set_locked_keyword_enabled(settings, account.id, lock_enabled)
        db.session.commit()
        return redirect(url_for("mail.settings"))
    spam_action_prefs = _load_spam_action_prefs(settings)
    from app.modules.mail.services.protection import load_locked_keyword_prefs
    locked_keyword_prefs = load_locked_keyword_prefs(settings)
    return render_template("settings.html", settings=settings, accounts=accounts, spam_action_prefs=spam_action_prefs, locked_keyword_prefs=locked_keyword_prefs, timezone_options=COMMON_TIMEZONES)


@mail_bp.route("/mail/settings/reset-cache", methods=["POST"])
@require_customer
def settings_reset_cache():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    account = None
    if account_id:
        account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first()
    if not account:
        account = CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).first()
    if not account:
        return redirect(url_for("mail.settings"))
    if not account.cache_db_path:
        account.cache_db_path = build_cache_path(user_id, account.id)
        db.session.commit()
    purge_cache(account.cache_db_path, key=get_user_key(user_id))
    current_app.sync_manager.enqueue_sync(account.id, folder="INBOX", reason="cache_reset", priority=0)
    current_app.sync_manager.enqueue_sync(account.id, folder="Sent", reason="cache_reset", priority=5)
    return redirect(url_for("mail.settings", cache_reset=1))
