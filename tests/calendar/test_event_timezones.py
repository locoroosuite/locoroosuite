"""Tests for TZID-aware event time display in the calendar module.

Regression tests for the bug where a Microsoft Exchange TZID (e.g.
"Cen. Australia Standard Time") failed IANA resolution and silently
defaulted to UTC on the event detail "When" row and the grid payload.
"""

from app.modules.calendar.controllers.events import (
    _format_event_date_range,
    _format_event_time,
)
from app.modules.calendar.controllers.views import _resolve_event_tz_name
from tests.shared.test_tzid_resolver import EXCHANGE_INVITE_ICS


def test_format_event_time_windows_tzid():
    # Noon in Adelaide must render as noon for an Adelaide user.
    assert (
        _format_event_time(
            "2026-08-27T12:00:00", "Australia/Adelaide", "Cen. Australia Standard Time"
        )
        == "Thu, Aug 27, 2026 at 12:00 PM"
    )


def test_format_event_time_windows_tzid_converted():
    assert (
        _format_event_time("2026-08-27T12:00:00", "Europe/Madrid", "Cen. Australia Standard Time")
        == "Thu, Aug 27, 2026 at 04:30 AM"
    )


def test_format_event_time_iana_tzid_unchanged():
    assert (
        _format_event_time("2026-08-27T12:00:00", "Europe/Madrid", "Europe/Berlin")
        == "Thu, Aug 27, 2026 at 12:00 PM"
    )


def test_format_event_time_custom_tzid_via_vtimezone():
    # A custom (non-Windows, non-IANA) TZID resolves via the embedded
    # VTIMEZONE: winter in the southern zone = +09:30.
    from tests.shared.test_tzid_resolver import CUSTOM_TZ_VTIMEZONES

    assert (
        _format_event_time(
            "2026-08-27T12:00:00",
            "Europe/Madrid",
            "Custom/Zone",
            vtimezones=CUSTOM_TZ_VTIMEZONES,
        )
        == "Thu, Aug 27, 2026 at 04:30 AM"
    )


def test_format_event_date_range_uses_raw_ical():
    event = {
        "all_day": 0,
        "dtstart": "2026-08-27T12:00:00",
        "dtend": "2026-08-27T12:45:00",
        "timezone": "Cen. Australia Standard Time",
        "raw_ical": EXCHANGE_INVITE_ICS,
    }
    assert (
        _format_event_date_range(event, "Australia/Adelaide")
        == "Thu, Aug 27, 2026 at 12:00 PM \u2013 12:45 PM"
    )


def test_format_event_date_range_all_day_ignores_timezone():
    event = {
        "all_day": 1,
        "dtstart": "2026-08-27",
        "dtend": "2026-08-28",
        "timezone": "Cen. Australia Standard Time",
    }
    assert _format_event_date_range(event, "Australia/Adelaide") == "2026-08-27 \u2013 2026-08-28"


def test_resolve_event_tz_name_windows_tzid():
    event = {"timezone": "Cen. Australia Standard Time", "raw_ical": EXCHANGE_INVITE_ICS}
    assert _resolve_event_tz_name(event) == "Australia/Adelaide"


def test_resolve_event_tz_name_windows_tzid_without_raw_ical():
    # The Windows map alone resolves the TZID; raw_ical is not required.
    event = {"timezone": "Cen. Australia Standard Time", "raw_ical": ""}
    assert _resolve_event_tz_name(event) == "Australia/Adelaide"


def test_resolve_event_tz_name_iana_passthrough():
    event = {"timezone": "Europe/Madrid", "raw_ical": ""}
    assert _resolve_event_tz_name(event) == "Europe/Madrid"


def test_resolve_event_tz_name_empty():
    assert _resolve_event_tz_name({"timezone": "", "raw_ical": ""}) == ""
