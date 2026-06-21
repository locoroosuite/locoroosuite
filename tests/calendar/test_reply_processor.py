import json
import os
import tempfile
from unittest.mock import patch, MagicMock



SAMPLE_ICS_REPLY_ACCEPTED = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REPLY\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:reply-test-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED;RSVP=FALSE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SAMPLE_ICS_REPLY_DECLINED = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REPLY\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:reply-test-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;CN=Bob;PARTSTAT=DECLINED;RSVP=FALSE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SAMPLE_ICS_REQUEST = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REQUEST\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:reply-test-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "ATTENDEE;CN=Bob;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _create_temp_cache():
    from app.modules.calendar.services.cache_db import open_cache
    import os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    key_hex = "0" * 64
    conn = open_cache(path, key_hex)
    return conn, path, key_hex


class TestProcessIncomingReply:
    def test_reply_accepted(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [{"cn": "Bob", "email": "bob@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            event_id = cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            result = process_incoming_reply(conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com")
            assert result is True

            event = cache_db.get_event(conn, event_id)
            attendees = json.loads(event["attendees"])
            assert len(attendees) == 1
            assert attendees[0]["partstat"] == "ACCEPTED"
            assert attendees[0]["rsvp"] == "FALSE"
            assert "ACCEPTED" in event["raw_ical"]
            assert "NEEDS-ACTION" not in event["raw_ical"]
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_declined(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [{"cn": "Bob", "email": "bob@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            event_id = cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            result = process_incoming_reply(conn, SAMPLE_ICS_REPLY_DECLINED, "bob@example.com")
            assert result is True

            event = cache_db.get_event(conn, event_id)
            attendees = json.loads(event["attendees"])
            assert attendees[0]["partstat"] == "DECLINED"
            assert "DECLINED" in event["raw_ical"]
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_not_reply_method(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply

        conn, path, key = _create_temp_cache()
        try:
            result = process_incoming_reply(conn, SAMPLE_ICS_REQUEST, "bob@example.com")
            assert result is False
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_event_not_found(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply

        conn, path, key = _create_temp_cache()
        try:
            result = process_incoming_reply(conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com")
            assert result is False
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_attendee_not_found(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [{"cn": "Charlie", "email": "charlie@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            result = process_incoming_reply(conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com")
            assert result is False
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_empty_ical(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply

        conn, path, key = _create_temp_cache()
        try:
            result = process_incoming_reply(conn, "", "bob@example.com")
            assert result is False
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_updates_only_matching_attendee(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [
                    {"cn": "Bob", "email": "bob@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"},
                    {"cn": "Charlie", "email": "charlie@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"},
                ],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            event_id = cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            result = process_incoming_reply(conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com")
            assert result is True

            event = cache_db.get_event(conn, event_id)
            attendees = json.loads(event["attendees"])
            assert len(attendees) == 2
            assert attendees[0]["partstat"] == "ACCEPTED"
            assert attendees[1]["partstat"] == "NEEDS-ACTION"

            assert "ACCEPTED" in event["raw_ical"]
            charlie_line = [line for line in event["raw_ical"].splitlines() if "charlie@example.com" in line][0]
            assert "NEEDS-ACTION" in charlie_line
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_updates_raw_ical(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [{"cn": "Bob", "email": "bob@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            event_id = cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            original_raw = cache_db.get_event(conn, event_id)["raw_ical"]
            assert "NEEDS-ACTION" in original_raw

            process_incoming_reply(conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com")

            updated_raw = cache_db.get_event(conn, event_id)["raw_ical"]
            assert "PARTSTAT=ACCEPTED" in updated_raw
            assert "NEEDS-ACTION" not in updated_raw
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_with_account_tries_caldav_push(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [{"cn": "Bob", "email": "bob@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            event_id = cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            mock_account = MagicMock()
            mock_account.domain_id = 1
            mock_account.customer_id = 1
            mock_account.encrypted_secret = None

            with patch("app.modules.calendar.services.reply_processor._push_to_caldav") as mock_push:
                result = process_incoming_reply(
                    conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com", account=mock_account
                )
                assert result is True
                mock_push.assert_called_once()

            event = cache_db.get_event(conn, event_id)
            attendees = json.loads(event["attendees"])
            assert attendees[0]["partstat"] == "ACCEPTED"
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_caldav_failure_does_not_block_cache_update(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [{"cn": "Bob", "email": "bob@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            event_id = cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            mock_account = MagicMock()

            with patch(
                "app.modules.calendar.services.reply_processor._push_to_caldav",
                side_effect=Exception("caldav connection refused"),
            ):
                result = process_incoming_reply(
                    conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com", account=mock_account
                )
                assert result is True

            event = cache_db.get_event(conn, event_id)
            attendees = json.loads(event["attendees"])
            assert attendees[0]["partstat"] == "ACCEPTED"
            assert "ACCEPTED" in event["raw_ical"]
        finally:
            conn.close()
            os.unlink(path)

    def test_reply_without_account_skips_caldav_push(self):
        from app.modules.calendar.services.reply_processor import process_incoming_reply
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
            ical_data = {
                "summary": "Team Meeting",
                "dtstart": "20260615T100000Z",
                "dtend": "20260615T110000Z",
                "attendees": [{"cn": "Bob", "email": "bob@example.com", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
            }
            from app.shared.icalendar import generate_icalendar
            ical = generate_icalendar(ical_data, uid="reply-test-uid@example.com")
            cache_db.upsert_event(conn, "reply-test-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            with patch("app.modules.calendar.services.reply_processor._push_to_caldav") as mock_push:
                result = process_incoming_reply(conn, SAMPLE_ICS_REPLY_ACCEPTED, "bob@example.com")
                assert result is True
                mock_push.assert_not_called()
        finally:
            conn.close()
            os.unlink(path)


class TestPatchRawIcalAttendee:
    def test_patch_existing_partstat(self):
        from app.modules.calendar.services.reply_processor import _patch_raw_ical_attendee

        raw = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\n"
            "ATTENDEE;CN=Bob;PARTSTAT=NEEDS-ACTION;ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:bob@example.com\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        result = _patch_raw_ical_attendee(raw, "bob@example.com", "ACCEPTED")
        assert "PARTSTAT=ACCEPTED" in result
        assert "NEEDS-ACTION" not in result

    def test_patch_preserves_other_attendees(self):
        from app.modules.calendar.services.reply_processor import _patch_raw_ical_attendee

        raw = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\n"
            "ATTENDEE;CN=Bob;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:bob@example.com\r\n"
            "ATTENDEE;CN=Charlie;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:charlie@example.com\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        result = _patch_raw_ical_attendee(raw, "bob@example.com", "ACCEPTED")
        assert "PARTSTAT=ACCEPTED" in result
        charlie_line = [line for line in result.splitlines() if "charlie" in line][0]
        assert "NEEDS-ACTION" in charlie_line

    def test_patch_empty_ical(self):
        from app.modules.calendar.services.reply_processor import _patch_raw_ical_attendee
        assert _patch_raw_ical_attendee("", "bob@example.com", "ACCEPTED") == ""

    def test_patch_case_insensitive_email(self):
        from app.modules.calendar.services.reply_processor import _patch_raw_ical_attendee

        raw = (
            "ATTENDEE;CN=Bob;PARTSTAT=NEEDS-ACTION:mailto:Bob@Example.COM\r\n"
        )
        result = _patch_raw_ical_attendee(raw, "bob@example.com", "TENTATIVE")
        assert "PARTSTAT=TENTATIVE" in result
