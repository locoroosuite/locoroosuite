import logging
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def resolve_user_timezone(settings_timezone):
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
            try:
                flask_session.pop("_browser_tz", None)
            except RuntimeError:
                pass

    return "UTC"


def resolve_tzinfo(settings_timezone):
    name = resolve_user_timezone(settings_timezone)
    if name == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc
