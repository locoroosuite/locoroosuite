import logging

from flask import current_app, jsonify, render_template, request, session
from flask_babel import _

from app.modules.mail.controllers.helpers import (
    _get_or_create_settings,
    _imap_for_account,
    mail_bp,
)
from app.modules.mail.services.cache import build_cache_path, purge_cache
from app.modules.mail.services.imap_client import safe_logout
from app.modules.mail.services.protection import (
    load_locked_keyword_prefs,
    set_locked_keyword_enabled,
)
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.spam import (
    maybe_reenable_auto_disabled as _maybe_reenable_auto_disabled,
)
from app.modules.mail.services.spam import set_spam_action_enabled as _set_spam_action_enabled
from app.modules.mail.services.spam import (
    spam_action_auto_disabled as _spam_action_auto_disabled,
)
from app.modules.mail.services.spam import spam_action_enabled as _spam_action_enabled
from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.display import TEXT_SCALE_CHOICES
from app.shared.i18n import LANGUAGE_SETTING_CHOICES, LANGUAGE_SETTING_LABELS
from app.shared.keys import get_user_key
from app.shared.models.core import CustomerAccount
from app.shared.timezone import COMMON_TIMEZONES

_logger = logging.getLogger(__name__)

POLL_INTERVAL_CHOICES = (30, 60, 120, 300, 600, 1800)
SORT_ORDER_CHOICES = ("date_desc", "date_asc")
THEME_CHOICES = ("light", "dark")


def _polling_choices():
    # Built per-request so the labels go through gettext (extractable literals).
    return [
        (30, _("Every 30 seconds")),
        (60, _("Every minute")),
        (120, _("Every 2 minutes")),
        (300, _("Every 5 minutes")),
        (600, _("Every 10 minutes")),
        (1800, _("Every 30 minutes")),
    ]


_BOOL_FIELDS = (
    "preview_pane_default",
    "protect_starred",
    "push_detailed",
    "notify_mail_enabled",
    "notify_calendar_enabled",
    "notify_chat_enabled",
)
_ACCOUNT_PREFIXES = {
    "spam_action_": _set_spam_action_enabled,
    "locked_keyword_": set_locked_keyword_enabled,
}


def _pref_error(code: str, message: str, status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _recover_auto_disabled_spam(user_id, settings, accounts):
    """U9.5: re-probe auto-disabled spam accounts; re-enable healthy ones.

    A failed IMAP connection is logged and skipped (the probe retries the
    next time the page loads); only `auto_off` accounts are touched, never a
    deliberate user toggle.
    """
    candidates = [a for a in accounts if _spam_action_auto_disabled(settings, a.id)]
    if not candidates:
        return
    key = get_user_key(user_id)
    changed = False
    for account in candidates:
        secret = (
            decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
        )
        try:
            client, _domain = _imap_for_account(account, secret)
        except Exception:
            _logger.warning(
                "spam recovery probe could not connect account_id=%s",
                account.id,
                exc_info=True,
            )
            continue
        try:
            changed = _maybe_reenable_auto_disabled(settings, account.id, client) or changed
        finally:
            safe_logout(client)
    if changed:
        db.session.commit()


@mail_bp.route("/mail/settings", methods=["GET"])
@require_customer
def settings():
    user_id = session.get("user_id")
    settings = _get_or_create_settings(user_id)
    accounts = CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).all()
    _recover_auto_disabled_spam(user_id, settings, accounts)
    locked_keyword_prefs = load_locked_keyword_prefs(settings)
    polling_choices = _polling_choices()
    spam_enabled_map = {str(a.id): _spam_action_enabled(settings, a.id) for a in accounts}
    spam_auto_disabled_ids = [a.id for a in accounts if _spam_action_auto_disabled(settings, a.id)]
    return render_template(
        "settings.html",
        settings=settings,
        accounts=accounts,
        spam_enabled_map=spam_enabled_map,
        spam_auto_disabled_ids=spam_auto_disabled_ids,
        locked_keyword_prefs=locked_keyword_prefs,
        timezone_options=COMMON_TIMEZONES,
        polling_choices=polling_choices,
        language_choices=LANGUAGE_SETTING_CHOICES,
        language_labels=dict(LANGUAGE_SETTING_LABELS),
    )


def _account_for_key(user_id, key: str, prefix: str):
    if user_id is None:
        return None
    raw_id = key[len(prefix) :]
    try:
        account_id = int(raw_id)
    except ValueError:
        return None
    return CustomerAccount.query.filter_by(
        id=account_id, customer_id=user_id, is_active=True
    ).first()


@mail_bp.route("/mail/settings/pref", methods=["POST"])
@require_customer
def settings_pref():
    user_id = session.get("user_id")
    settings = _get_or_create_settings(user_id)
    payload = request.get_json(silent=True) or {}
    key = payload.get("key")
    value = payload.get("value")
    if not isinstance(key, str) or not key:
        return _pref_error("INVALID_KEY", _("A setting key is required."))

    if key in _BOOL_FIELDS:
        setattr(settings, key, value is True)
    elif key == "polling_interval":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return _pref_error(
                "INVALID_VALUE", _("Sync frequency must be one of the preset values.")
            )
        try:
            interval = int(value)
        except ValueError:
            return _pref_error(
                "INVALID_VALUE", _("Sync frequency must be one of the preset values.")
            )
        if interval not in POLL_INTERVAL_CHOICES:
            return _pref_error(
                "INVALID_VALUE", _("Sync frequency must be one of the preset values.")
            )
        settings.polling_interval = interval
    elif key == "sort_order":
        if value not in SORT_ORDER_CHOICES:
            return _pref_error(
                "INVALID_VALUE", _("Sort order must be newest-first or oldest-first.")
            )
        settings.sort_order = value
    elif key == "timezone":
        tz_val = str(value or "").strip()
        if tz_val != "browser" and tz_val not in COMMON_TIMEZONES:
            return _pref_error("INVALID_VALUE", _("Unknown timezone."))
        settings.timezone = tz_val
    elif key == "language":
        lang_val = str(value or "").strip()
        if lang_val not in LANGUAGE_SETTING_CHOICES:
            return _pref_error("INVALID_VALUE", _("Language must be auto, English, or Spanish."))
        settings.language = lang_val
    elif key == "theme":
        if value not in THEME_CHOICES:
            return _pref_error("INVALID_VALUE", _("Theme must be light or dark."))
        settings.theme = value
    elif key == "text_scale":
        if value not in TEXT_SCALE_CHOICES:
            return _pref_error(
                "INVALID_VALUE", _("Text size must be default, large, or larger.")
            )
        settings.text_scale = value
    else:
        for prefix, setter in _ACCOUNT_PREFIXES.items():
            if key.startswith(prefix):
                account = _account_for_key(user_id, key, prefix)
                if not account:
                    return _pref_error("ACCOUNT_NOT_FOUND", _("No such active account."), 404)
                setter(settings, account.id, value is True)
                break
        else:
            return _pref_error("UNKNOWN_SETTING", _("Unknown setting."))

    db.session.commit()
    return jsonify({"status": "saved", "key": key})


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
        return jsonify(
            {"error": {"code": "ACCOUNT_NOT_FOUND", "message": _("No active account.")}}
        ), 404
    if not account.cache_db_path:
        account.cache_db_path = build_cache_path(user_id, account.id)
        db.session.commit()
    purge_cache(account.cache_db_path, key=get_user_key(user_id))
    sync_manager = getattr(current_app, "sync_manager", None)
    if sync_manager is None:
        return (
            jsonify(
                {
                    "error": {
                        "code": "SYNC_UNAVAILABLE",
                        "message": _(
                            "Cache cleared, but background sync is unavailable. Re-open a folder to re-sync."
                        ),
                    }
                }
            ),
            503,
        )
    sync_manager.enqueue_sync(account.id, folder="INBOX", reason="cache_reset", priority=0)
    sync_manager.enqueue_sync(account.id, folder="Sent", reason="cache_reset", priority=5)
    return jsonify({"status": "ok", "account": account.email_address})
