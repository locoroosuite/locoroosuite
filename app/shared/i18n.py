"""Locale resolution for Flask-Babel (HLD U26).

Resolution order (first match wins):
1. Authenticated customer's ``CustomerSettings.language`` (explicit locale).
2. ``browser_lang`` cookie (set by JS from ``navigator.languages``).
3. ``Accept-Language`` negotiation — any Spanish variant maps to the single
   ``es_ES`` catalog until a specific variant catalog exists (U26.2).
4. English.

Admin/manager sessions are pinned to English: the admin area is
English-only (U26.5) and the shared layout renders its chrome.
"""

import logging
import os

from flask import current_app, g, request, session

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("en", "es_ES")
DEFAULT_LOCALE = "en"
BROWSER_LANG_COOKIE = "browser_lang"
LANGUAGE_SETTING_CHOICES = ("browser", "en", "es")
LANGUAGE_SETTING_LABELS = (
    ("browser", "Auto (browser)"),
    ("en", "English"),
    ("es", "Español"),
)


def _locale_for_tag(tag: str) -> str | None:
    """Map a BCP-47 tag or gettext locale name to a supported locale (U26.2)."""
    primary = (tag or "").strip().lower().replace("_", "-").split("-")[0]
    if primary == "es":
        return "es_ES"
    if primary == "en":
        return "en"
    return None


def _locale_from_setting(value) -> str | None:
    raw = (value or "").strip()
    if not raw or raw.lower() == "browser":
        return None
    return _locale_for_tag(raw)


def _setting_locale() -> str | None:
    role = session.get("role")
    user_id = session.get("user_id")
    if role != "customer" or not user_id:
        return None
    from app.shared.db import db
    from app.shared.models.core import CustomerSettings

    settings = db.session.get(CustomerSettings, user_id)
    if settings is None:
        return None
    return _locale_from_setting(settings.language)


def _cookie_locale() -> str | None:
    raw = request.cookies.get(BROWSER_LANG_COOKIE, "")
    return _locale_for_tag(raw)


def _accept_language_locale() -> str | None:
    try:
        for tag, _quality in request.accept_languages:
            locale = _locale_for_tag(tag)
            if locale:
                return locale
    except Exception:
        logger.debug("failed to parse Accept-Language", exc_info=True)
    return None


def resolve_locale() -> str | None:
    """Flask-Babel ``locale_selector_func``. ``None`` falls back to English."""
    if session.get("role") in ("admin", "manager"):
        return DEFAULT_LOCALE
    if locale := _setting_locale():
        return locale
    if locale := _cookie_locale():
        return locale
    return _accept_language_locale()


def current_locale_name() -> str:
    """Current locale as a catalog name ("en" / "es_ES") for date formatting."""
    try:
        babel_locale = g.get("_lr_locale")
    except RuntimeError:  # outside app/request context (workers) → English
        return DEFAULT_LOCALE
    if babel_locale:
        name = str(babel_locale)
        if name in SUPPORTED_LOCALES:
            return name
        return _locale_for_tag(name) or DEFAULT_LOCALE
    return DEFAULT_LOCALE


def html_lang() -> str:
    """Value for the ``<html lang="...">`` attribute ("en" / "es-ES")."""
    name = current_locale_name()
    if "_" in name:
        lang, region = name.split("_", 1)
        return f"{lang.lower()}-{region.upper()}"
    return name.lower()


def N_(msgid: str) -> str:
    """gettext extraction marker, no-op at runtime (classic gettext_noop).

    Use for msgids selected at runtime and translated later via
    ``translate_for_user`` (e.g. worker-thread error mapping) so
    ``pybabel extract`` keeps them in the catalog.
    """
    return msgid


def locale_for_user(user_id) -> str:
    """Resolve a user's locale outside a request (workers, push, background sends).

    Order: explicit ``CustomerSettings.language`` → cached ``browser_locale``
    (persisted by ``_persist_browser_locale`` when the user browses with
    language=auto) → English. Requires an application context.
    """
    from app.shared.db import db
    from app.shared.models.core import CustomerSettings

    settings = db.session.get(CustomerSettings, user_id)
    if settings is None:
        return DEFAULT_LOCALE
    locale = _locale_from_setting(getattr(settings, "language", None))
    if locale:
        return locale
    cached = (getattr(settings, "browser_locale", None) or "").strip()
    return _locale_for_tag(cached) or DEFAULT_LOCALE


def forced_user_locale(user_id):
    """Context manager forcing the user's locale for worker-side translation.

    Use inside an application context::

        with forced_user_locale(user_id):
            title = _("New email")
            when = format_datetime(dt, "EEE, MMM dd, y")
    """
    from flask_babel import force_locale

    return force_locale(locale_for_user(user_id))


def translate_for_user(user_id, msgid: str, **kwargs) -> str:
    """One-shot translation of ``msgid`` in the user's locale (worker-safe)."""
    from flask_babel import gettext

    with forced_user_locale(user_id):
        return gettext(msgid, **kwargs)


def _persist_browser_locale() -> None:
    """Cache the resolved browser locale on CustomerSettings for worker-side use.

    Only writes when the value changes, only for customers whose language is
    "auto (browser)". Never allowed to break the request.
    """
    if session.get("role") != "customer" or not session.get("user_id"):
        return
    if request.path.startswith(("/static/", "/app/i18n/")):
        return
    locale = current_locale_name()
    if locale == DEFAULT_LOCALE:
        return
    from app.shared.db import db
    from app.shared.models.core import CustomerSettings

    settings = db.session.get(CustomerSettings, session["user_id"])
    if settings is None:
        return
    language = (getattr(settings, "language", None) or "").strip()
    if language and language.lower() != "browser":
        return  # explicit choice wins everywhere; cache is not consulted
    if (getattr(settings, "browser_locale", None) or "") == locale:
        return
    settings.browser_locale = locale
    db.session.commit()


def catalog_messages() -> dict[str, str]:
    """Message catalog for the current locale (empty dict = passthrough).

    Plural msgids are stored by gettext as ``(singular, plural)`` tuple keys;
    they are not representable in the flat JS catalog and are skipped.
    """
    if current_locale_name() == DEFAULT_LOCALE:
        return {}
    from flask_babel import get_translations

    translations = get_translations()
    catalog = getattr(translations, "_catalog", None)
    if not isinstance(catalog, dict):
        return {}
    return {
        msgid: msgstr
        for msgid, msgstr in catalog.items()
        if isinstance(msgid, str) and msgid and msgstr
    }


def catalog_version() -> str:
    """Cache-busting version for the served JS catalog (mtime of the .mo)."""
    mo_path = os.path.join(
        current_app.root_path, "translations", "es_ES", "LC_MESSAGES", "messages.mo"
    )
    try:
        return str(int(os.path.getmtime(mo_path)))
    except OSError:
        return "0"


def register_i18n(app) -> None:
    """Initialize Flask-Babel, the JS catalog route, and template globals."""
    from flask_babel import Babel, get_locale

    Babel(
        app,
        locale_selector=resolve_locale,
        default_locale=DEFAULT_LOCALE,
        default_translation_directories="translations",
    )  # flask-babel registers itself in app.extensions["babel"]

    @app.before_request
    def _capture_locale():
        try:
            g._lr_locale = get_locale()
        except Exception:
            logger.debug("failed to capture locale", exc_info=True)
            g._lr_locale = None
        try:
            _persist_browser_locale()
        except Exception:
            logger.debug("failed to persist browser locale", exc_info=True)
            from app.shared.db import db

            db.session.rollback()

    @app.route("/app/i18n/messages.js")
    def i18n_messages_js():
        import json

        payload = json.dumps(catalog_messages(), ensure_ascii=False, sort_keys=True)
        locale = current_locale_name()
        body = (
            f"window.LR_I18N = {{locale: {json.dumps(locale)}, messages: {payload}}};\n"
            "(window.LR = window.LR || {}).t = function(key, params) {\n"
            "  var msg = (window.LR_I18N && window.LR_I18N.messages[key]) || key;\n"
            "  if (params) {\n"
            "    Object.keys(params).forEach(function(p) {\n"
            "      msg = msg.split('{' + p + '}').join(String(params[p]));\n"
            "    });\n"
            "  }\n"
            "  return msg;\n"
            "};\n"
        )
        response = current_app.response_class(body, mimetype="text/javascript")
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    app.add_template_global(current_locale_name, "current_locale")
    app.add_template_global(html_lang, "html_lang")
    app.add_template_global(catalog_version, "i18n_v")
