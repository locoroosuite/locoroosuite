from app.modules.calendar.services.icalendar import parse_icalendar, generate_icalendar, extract_uid


def test_parse_basic_event():
    ical = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-1
SUMMARY:Team Meeting
DESCRIPTION:Weekly sync
LOCATION:Room 101
DTSTART:20250115T100000Z
DTEND:20250115T110000Z
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR"""
    result = parse_icalendar(ical)
    assert result["uid"] == "test-uid-1"
    assert result["summary"] == "Team Meeting"
    assert result["description"] == "Weekly sync"
    assert result["location"] == "Room 101"
    assert "2025-01-15" in result["dtstart"]
    assert "2025-01-15" in result["dtend"]
    assert result["status"] == "CONFIRMED"
    assert result["all_day"] is False


def test_parse_all_day_event():
    ical = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-2
SUMMARY:Holiday
DTSTART;VALUE=DATE:20250115
DTEND;VALUE=DATE:20250116
END:VEVENT
END:VCALENDAR"""
    result = parse_icalendar(ical)
    assert result["uid"] == "test-uid-2"
    assert result["summary"] == "Holiday"
    assert result["all_day"] is True
    assert result["dtstart"] == "2025-01-15"
    assert result["dtend"] == "2025-01-16"


def test_parse_recurring_event():
    ical = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-3
SUMMARY:Daily Standup
DTSTART:20250115T090000Z
DTEND:20250115T091500Z
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR
END:VEVENT
END:VCALENDAR"""
    result = parse_icalendar(ical)
    assert result["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE,FR"


def test_parse_with_attendees():
    ical = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-4
SUMMARY:Review
DTSTART:20250115T100000Z
ORGANIZER;CN=Alice:mailto:alice@example.com
ATTENDEE;CN=Bob;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:bob@example.com
ATTENDEE;CN=Carol;ROLE=OPT-PARTICIPANT;PARTSTAT=DECLINED;RSVP=FALSE:mailto:carol@example.com
END:VEVENT
END:VCALENDAR"""
    result = parse_icalendar(ical)
    assert result["organizer"]["cn"] == "Alice"
    assert result["organizer"]["email"] == "alice@example.com"
    assert len(result["attendees"]) == 2
    assert result["attendees"][0]["cn"] == "Bob"
    assert result["attendees"][0]["partstat"] == "ACCEPTED"
    assert result["attendees"][1]["cn"] == "Carol"
    assert result["attendees"][1]["partstat"] == "DECLINED"


def test_parse_with_alarm():
    ical = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-5
SUMMARY:Reminder Test
DTSTART:20250115T100000Z
BEGIN:VALARM
TRIGGER:-PT15M
ACTION:DISPLAY
DESCRIPTION:Meeting in 15 min
END:VALARM
END:VEVENT
END:VCALENDAR"""
    result = parse_icalendar(ical)
    assert len(result["alarms"]) == 1
    assert result["alarms"][0]["trigger"] == "-PT15M"
    assert result["alarms"][0]["action"] == "DISPLAY"
    assert result["alarms"][0]["description"] == "Meeting in 15 min"


def test_generate_basic_event():
    data = {
        "summary": "Test Event",
        "description": "A test",
        "location": "Room 1",
        "dtstart": "2025-01-15T10:00:00+00:00",
        "dtend": "2025-01-15T11:00:00+00:00",
        "all_day": False,
        "status": "CONFIRMED",
    }
    result = generate_icalendar(data, uid="gen-uid-1")
    assert "BEGIN:VCALENDAR" in result
    assert "BEGIN:VEVENT" in result
    assert "UID:gen-uid-1" in result
    assert "SUMMARY:Test Event" in result
    assert "DESCRIPTION:A test" in result
    assert "LOCATION:Room 1" in result
    assert "STATUS:CONFIRMED" in result


def test_generate_all_day_event():
    data = {
        "summary": "All Day",
        "dtstart": "2025-01-15",
        "dtend": "2025-01-16",
        "all_day": True,
    }
    result = generate_icalendar(data, uid="gen-uid-2")
    assert "DTSTART;VALUE=DATE:20250115" in result
    assert "DTEND;VALUE=DATE:20250116" in result


def test_generate_with_reminder():
    data = {
        "summary": "With Reminder",
        "dtstart": "2025-01-15T10:00:00+00:00",
        "alarms": [{"trigger": "-PT30M", "action": "DISPLAY", "description": "30 min warning"}],
    }
    result = generate_icalendar(data, uid="gen-uid-3")
    assert "BEGIN:VALARM" in result
    assert "TRIGGER:-PT30M" in result
    assert "ACTION:DISPLAY" in result
    assert "DESCRIPTION:30 min warning" in result
    assert "END:VALARM" in result


def test_extract_uid():
    ical = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:abc-123\r\nSUMMARY:Test\r\nEND:VEVENT\r\nEND:VCALENDAR"
    assert extract_uid(ical) == "abc-123"


def test_extract_uid_missing():
    ical = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Test\r\nEND:VEVENT\r\nEND:VCALENDAR"
    assert extract_uid(ical) is None


def test_roundtrip():
    data = {
        "summary": "Round Trip",
        "description": "Test roundtrip",
        "location": "Room 42",
        "dtstart": "2025-01-15T10:00:00+00:00",
        "dtend": "2025-01-15T11:00:00+00:00",
        "all_day": False,
        "status": "TENTATIVE",
        "rrule": "FREQ=DAILY",
        "alarms": [{"trigger": "-PT15M", "action": "DISPLAY", "description": "Soon"}],
    }
    ical = generate_icalendar(data, uid="rt-1")
    parsed = parse_icalendar(ical)
    assert parsed["summary"] == "Round Trip"
    assert parsed["description"] == "Test roundtrip"
    assert parsed["location"] == "Room 42"
    assert parsed["status"] == "TENTATIVE"
    assert parsed["rrule"] == "FREQ=DAILY"
    assert len(parsed["alarms"]) == 1
    assert parsed["alarms"][0]["trigger"] == "-PT15M"


def test_parse_empty():
    assert parse_icalendar("") == {}
    assert parse_icalendar(None) == {}


def test_generate_with_timezone_includes_vtimezone():
    data = {
        "summary": "Adelaide Event",
        "dtstart": "2026-05-14T12:00:00",
        "dtend": "2026-05-14T13:00:00",
        "timezone": "Australia/Adelaide",
    }
    result = generate_icalendar(data, uid="vtz-1")
    assert "BEGIN:VTIMEZONE" in result
    assert "TZID:Australia/Adelaide" in result
    assert "END:VTIMEZONE" in result
    assert "DTSTART;TZID=Australia/Adelaide:20260514T120000" in result
    assert "DTEND;TZID=Australia/Adelaide:20260514T130000" in result


def test_generate_with_utc_has_no_vtimezone():
    data = {
        "summary": "UTC Event",
        "dtstart": "2025-01-15T10:00:00+00:00",
        "dtend": "2025-01-15T11:00:00+00:00",
    }
    result = generate_icalendar(data, uid="utc-1")
    assert "VTIMEZONE" not in result
    assert "DTSTART:20250115T100000Z" in result


def test_generate_vtimezone_no_dst():
    data = {
        "summary": "Tokyo Event",
        "dtstart": "2026-05-14T09:00:00",
        "dtend": "2026-05-14T10:00:00",
        "timezone": "Asia/Tokyo",
    }
    result = generate_icalendar(data, uid="vtz-tokyo")
    assert "BEGIN:VTIMEZONE" in result
    assert "TZID:Asia/Tokyo" in result
    assert "BEGIN:STANDARD" in result
    assert "DAYLIGHT" not in result


def test_generate_vtimezone_with_dst():
    data = {
        "summary": "Adelaide Event",
        "dtstart": "2026-05-14T12:00:00",
        "dtend": "2026-05-14T13:00:00",
        "timezone": "Australia/Adelaide",
    }
    result = generate_icalendar(data, uid="vtz-adl")
    assert "BEGIN:STANDARD" in result
    assert "BEGIN:DAYLIGHT" in result
    assert "TZOFFSETFROM" in result
    assert "TZOFFSETTO" in result
