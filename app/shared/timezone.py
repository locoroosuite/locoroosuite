import contextlib
import logging
from datetime import UTC
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import request as flask_request
from flask import session as flask_session

logger = logging.getLogger(__name__)

COMMON_TIMEZONES = [
    "Pacific/Honolulu",
    "Pacific/Auckland",
    "America/Anchorage",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "America/Caracas",
    "America/Sao_Paulo",
    "America/Argentina/Buenos_Aires",
    "America/Nuuk",
    "Atlantic/Azores",
    "Atlantic/Reykjavik",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Helsinki",
    "Europe/Bucharest",
    "Europe/Athens",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Dhaka",
    "Asia/Bangkok",
    "Asia/Singapore",
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Australia/Perth",
    "Australia/Adelaide",
    "Australia/Sydney",
    "UTC",
]


def resolve_user_timezone(settings_timezone, cached_browser_tz=None):
    """Resolve the user's timezone name.

    Resolution order (first match wins): explicit IANA setting → session
    ``_browser_tz`` (web requests; freshest) → ``cached_browser_tz``
    (``CustomerSettings.browser_tz``, persisted by ``persist_browser_tz``
    for session-less worker contexts) → UTC.
    """
    raw = (settings_timezone or "").strip()
    if raw and raw.lower() != "browser":
        try:
            ZoneInfo(raw)
            return raw
        except ZoneInfoNotFoundError:
            logger.debug("invalid user timezone setting: %s, falling back", raw)

    try:
        cached = flask_session.get("_browser_tz")
    except RuntimeError:
        cached = None
    if cached:
        try:
            ZoneInfo(cached)
            return cached
        except ZoneInfoNotFoundError:
            with contextlib.suppress(RuntimeError):
                flask_session.pop("_browser_tz", None)

    cached_setting = (cached_browser_tz or "").strip()
    if cached_setting:
        try:
            ZoneInfo(cached_setting)
            return cached_setting
        except ZoneInfoNotFoundError:
            logger.debug("invalid cached browser timezone: %s, falling back", cached_setting)

    return "UTC"


def persist_browser_tz() -> None:
    """Cache the session's browser timezone on CustomerSettings for worker-side use.

    Mirrors ``app.shared.i18n._persist_browser_locale``: only writes when the
    value changes, only for customers whose timezone is "browser" (explicit
    choices win everywhere; the cache is not consulted). Never allowed to
    break the request.
    """
    if flask_session.get("role") != "customer" or not flask_session.get("user_id"):
        return
    if flask_request.path.startswith(("/static/", "/app/i18n/")):
        return
    tz_name = flask_session.get("_browser_tz")
    if not tz_name:
        return
    from app.shared.db import db
    from app.shared.models.core import CustomerSettings

    settings = db.session.get(CustomerSettings, flask_session["user_id"])
    if settings is None:
        return
    timezone = (getattr(settings, "timezone", None) or "").strip()
    if timezone and timezone.lower() != "browser":
        return
    if (getattr(settings, "browser_tz", None) or "") == tz_name:
        return
    settings.browser_tz = tz_name
    db.session.commit()


def resolve_tzinfo(settings_timezone):
    name = resolve_user_timezone(settings_timezone)
    if name == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return UTC
