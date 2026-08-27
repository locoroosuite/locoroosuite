"""Tests for ICS invite-card date formatting in the mail module.

Regression tests for the bug where a Microsoft Exchange TZID (e.g.
"Cen. Australia Standard Time") failed IANA resolution and silently
defaulted to UTC, shifting displayed invite times by the zone offset.
"""

from app.modules.mail.controllers.helpers import (
    _format_ics_dates,
    _format_timed_range,
    _parse_ics_dt,
)
from app.shared.icalendar import parse_icalendar
from app.shared.timezone import resolve_tzinfo
from tests.shared.test_tzid_resolver import EXCHANGE_INVITE_ICS


def _offset_seconds(dt):
    assert dt is not None, "expected parsed datetime"
    offset = dt.utcoffset()
    assert offset is not None, "expected aware datetime"
    return offset.total_seconds()


def _invite_ics_attachment():
    parsed = parse_icalendar(EXCHANGE_INVITE_ICS)
    return {"parsed": parsed}


def test_exchange_invite_displays_local_adelaide_time():
    ics = _invite_ics_attachment()
    _format_ics_dates([ics], "Australia/Adelaide")
    assert ics["formatted_date"] == "Thu 27 Aug 2026, 12:00 PM \u2013 12:45 PM ACST"


def test_exchange_invite_converted_to_madrid():
    ics = _invite_ics_attachment()
    _format_ics_dates([ics], "Europe/Madrid")
    assert ics["formatted_date"] == "Thu 27 Aug 2026, 04:30 AM \u2013 05:15 AM CEST"


def test_exchange_invite_converted_to_utc():
    ics = _invite_ics_attachment()
    _format_ics_dates([ics], "UTC")
    assert ics["formatted_date"] == "Thu 27 Aug 2026, 02:30 AM \u2013 03:15 AM UTC"


def test_format_timed_range_without_vtimezones_still_resolves_windows_tzid():
    # The Windows TZID map works even when VTIMEZONE data is unavailable.
    result = _format_timed_range(
        "2026-08-27T12:00:00",
        "2026-08-27T12:45:00",
        "Cen. Australia Standard Time",
        resolve_tzinfo("Australia/Adelaide"),
    )
    assert result == "Thu 27 Aug 2026, 12:00 PM \u2013 12:45 PM ACST"


def test_format_timed_range_unknown_tzid_falls_back_to_utc():
    result = _format_timed_range(
        "2026-08-27T12:00:00",
        "2026-08-27T12:45:00",
        "Not A Real Zone",
        resolve_tzinfo("Australia/Adelaide"),
    )
    # Unresolvable TZID -> UTC assumption -> 9:30 PM ACST for an Adelaide user.
    assert result == "Thu 27 Aug 2026, 09:30 PM \u2013 10:15 PM ACST"


def test_parse_ics_dt_windows_tzid_aware():
    dt = _parse_ics_dt(
        "2026-08-27T12:00:00",
        "Cen. Australia Standard Time",
        vtimezones=parse_icalendar(EXCHANGE_INVITE_ICS)["vtimezones"],
    )
    assert _offset_seconds(dt) == 9.5 * 3600


def test_parse_ics_dt_utc_value_ignores_fallback():
    dt = _parse_ics_dt("2026-08-27T02:30:00+00:00", "Cen. Australia Standard Time")
    assert _offset_seconds(dt) == 0
