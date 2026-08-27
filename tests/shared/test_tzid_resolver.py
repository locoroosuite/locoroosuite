"""Tests for app.shared.tzid_resolver and VTIMEZONE parsing in app.shared.icalendar.

The EXCHANGE_INVITE_ICS below is a real Microsoft Exchange invite shape
(TZID "Cen. Australia Standard Time", embedded VTIMEZONE with DST rules).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.shared.icalendar import parse_icalendar
from app.shared.tzid_resolver import resolve_tzid, resolve_tzid_name
from app.shared.windows_tzid_map import WINDOWS_TZID_TO_IANA


def _offset_seconds(tz):
    assert tz is not None, "expected resolvable timezone"
    offset = tz.utcoffset(None)
    assert offset is not None, "expected fixed offset"
    return offset.total_seconds()


EXCHANGE_INVITE_ICS = """BEGIN:VCALENDAR
METHOD:REQUEST
PRODID:Microsoft Exchange Server 2010
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Cen. Australia Standard Time
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+1030
TZOFFSETTO:+0930
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=4
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+0930
TZOFFSETTO:+1030
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=10
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
SUMMARY:Rubén Rubio Rey meeting Gemma Agnew - Recruitment Conversation
DTSTART;TZID=Cen. Australia Standard Time:20260827T120000
DTEND;TZID=Cen. Australia Standard Time:20260827T124500
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR"""

CUSTOM_TZ_VTIMEZONES = {
    "Custom/Zone": [
        {
            "type": "STANDARD",
            "dtstart": "16010101T030000",
            "tzoffsetfrom": "+1030",
            "tzoffsetto": "+0930",
            "rrule": "FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=4",
        },
        {
            "type": "DAYLIGHT",
            "dtstart": "16010101T020000",
            "tzoffsetfrom": "+0930",
            "tzoffsetto": "+1030",
            "rrule": "FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=10",
        },
    ]
}


class TestParseIcalendarVtimezones:
    def test_exchange_invite_parses_windows_tzid_and_vtimezone(self):
        parsed = parse_icalendar(EXCHANGE_INVITE_ICS)
        assert parsed["timezone"] == "Cen. Australia Standard Time"
        assert parsed["dtstart"] == "2026-08-27T12:00:00"
        assert parsed["dtend"] == "2026-08-27T12:45:00"
        assert "Cen. Australia Standard Time" in parsed["vtimezones"]

    def test_vtimezone_observances_shape(self):
        parsed = parse_icalendar(EXCHANGE_INVITE_ICS)
        observances = parsed["vtimezones"]["Cen. Australia Standard Time"]
        by_type = {obs["type"]: obs for obs in observances}
        assert set(by_type) == {"STANDARD", "DAYLIGHT"}
        assert by_type["STANDARD"]["tzoffsetto"] == "+0930"
        assert by_type["DAYLIGHT"]["tzoffsetto"] == "+1030"
        assert by_type["STANDARD"]["rrule"] == "FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=4"

    def test_no_vtimezone_key_when_absent(self):
        parsed = parse_icalendar(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20250115T100000Z\nEND:VEVENT\nEND:VCALENDAR"
        )
        assert "vtimezones" not in parsed


class TestResolveTzid:
    def test_iana_tzid_direct(self):
        assert resolve_tzid("Europe/Madrid") == ZoneInfo("Europe/Madrid")

    def test_iana_tzid_leading_slash(self):
        assert resolve_tzid("/Europe/Berlin") == ZoneInfo("Europe/Berlin")

    def test_windows_tzid_mapped(self):
        tz = resolve_tzid("Cen. Australia Standard Time")
        assert tz == ZoneInfo("Australia/Adelaide")

    def test_all_windows_map_entries_resolve(self):
        unresolved = [
            tzid for tzid, iana in WINDOWS_TZID_TO_IANA.items() if resolve_tzid(tzid) is None
        ]
        assert unresolved == []

    def test_exchange_invite_resolves_via_map(self):
        parsed = parse_icalendar(EXCHANGE_INVITE_ICS)
        tz = resolve_tzid(
            parsed["timezone"], parsed.get("vtimezones"), datetime(2026, 8, 27, 12, 0)
        )
        assert tz == ZoneInfo("Australia/Adelaide")

    def test_unknown_tzid_returns_none(self):
        assert resolve_tzid("Not A Real Zone") is None

    def test_empty_tzid_returns_none(self):
        assert resolve_tzid("") is None
        assert resolve_tzid(None) is None

    def test_vtimezone_fixed_offset_winter(self):
        tz = resolve_tzid("Custom/Zone", CUSTOM_TZ_VTIMEZONES, datetime(2026, 8, 15, 12, 0))
        assert _offset_seconds(tz) == 9.5 * 3600

    def test_vtimezone_fixed_offset_dst(self):
        tz = resolve_tzid("Custom/Zone", CUSTOM_TZ_VTIMEZONES, datetime(2026, 1, 15, 12, 0))
        assert _offset_seconds(tz) == 10.5 * 3600

    @pytest.mark.parametrize(
        ("month", "hours"),
        [(1, 10.5), (4, 9.5), (8, 9.5), (10, 10.5), (12, 10.5)],
    )
    def test_vtimezone_dst_boundaries(self, month, hours):
        tz = resolve_tzid("Custom/Zone", CUSTOM_TZ_VTIMEZONES, datetime(2026, month, 15, 12, 0))
        assert _offset_seconds(tz) == hours * 3600

    def test_vtimezone_single_observance(self):
        tz = resolve_tzid(
            "Fixed/Zone",
            {"Fixed/Zone": [{"type": "STANDARD", "tzoffsetto": "+0545"}]},
            datetime(2026, 3, 1),
        )
        assert _offset_seconds(tz) == 5.75 * 3600

    def test_vtimezone_without_dt_falls_back_to_standard(self):
        tz = resolve_tzid("Custom/Zone", CUSTOM_TZ_VTIMEZONES)
        assert _offset_seconds(tz) == 9.5 * 3600

    def test_vtimezone_offset_with_colon(self):
        tz = resolve_tzid(
            "Colon/Zone",
            {"Colon/Zone": [{"type": "STANDARD", "tzoffsetto": "+10:30"}]},
            datetime(2026, 3, 1),
        )
        assert _offset_seconds(tz) == 10.5 * 3600


class TestResolveTzidName:
    def test_iana(self):
        assert resolve_tzid_name("Europe/Madrid") == "Europe/Madrid"

    def test_windows_mapped_to_iana(self):
        assert resolve_tzid_name("Cen. Australia Standard Time") == "Australia/Adelaide"

    def test_vtimezone_zone_keeps_original_tzid(self):
        assert resolve_tzid_name("Custom/Zone", CUSTOM_TZ_VTIMEZONES) == "Custom/Zone"

    def test_unknown_returns_none(self):
        assert resolve_tzid_name("Not A Real Zone") is None
