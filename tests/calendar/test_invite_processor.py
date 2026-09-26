import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from app.shared.icalendar import _unfold_lines

# METHOD:REQUEST invite where bob@example.com is an attendee of alice's event.
SAMPLE_ICS_REQUEST = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REQUEST\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:invite-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;CN=Bob;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

# Same UID but bob is NOT an attendee (invite for charlie only).
SAMPLE_ICS_REQUEST_OTHER_ATTENDEE = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REQUEST\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:invite-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;CN=Charlie;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:charlie@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SAMPLE_ICS_REPLY = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REPLY\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:invite-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED;RSVP=FALSE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SAMPLE_ICS_CANCEL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:CANCEL\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:invite-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _unfold(text):
    """Unfold RFC 5545 folded content lines for substring assertions."""
    return "\n".join(_unfold_lines(text.splitlines()))


def _create_temp_cache():
    from app.modules.calendar.services.cache_db import open_cache

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    key_hex = "0" * 64
    conn = open_cache(path, key_hex)
    return conn, path, key_hex


def _mock_account(email="bob@example.com", account_id=7):
    account = MagicMock()
    account.email_address = email
    account.id = account_id
    return account


class TestProcessIncomingInvite:
    def test_request_auto_imports_silent_tentative(self):
        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.invite_processor import process_incoming_invite

        conn, path, _key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Main")
            with (
                patch(
                    "app.modules.calendar.services.invite_processor._push_to_caldav",
                    return_value=("/cal1/invite-uid@example.com.ics", "etag-1"),
                ) as mock_push,
                patch("app.modules.calendar.services.imip.send_reply_imip") as mock_reply,
            ):
                result = process_incoming_invite(
                    conn, SAMPLE_ICS_REQUEST, _mock_account(), message_id=42
                )

            assert result is True
            mock_push.assert_called_once()
            mock_reply.assert_not_called()

            event = cache_db.get_event_by_uid(conn, "invite-uid@example.com")
            assert event is not None
            assert event["calendar_id"] == cal_id
            assert event["source_email_message_id"] == 42
            assert event["source_email_account_id"] == 7

            attendees = json.loads(event["attendees"])
            bob = next(a for a in attendees if a["email"] == "bob@example.com")
            assert bob["partstat"] == "TENTATIVE"
            assert bob["rsvp"] is True

            unfolded = _unfold(event["raw_ical"])
            assert "PARTSTAT=TENTATIVE" in unfolded
            assert "METHOD:REQUEST" not in unfolded
        finally:
            conn.close()
            os.unlink(path)

    def test_request_without_message_id_omits_source_link(self):
        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.invite_processor import process_incoming_invite

        conn, path, _key = _create_temp_cache()
        try:
            cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Main")
            with patch(
                "app.modules.calendar.services.invite_processor._push_to_caldav",
                return_value=("/cal1/invite-uid@example.com.ics", "etag-1"),
            ):
                result = process_incoming_invite(conn, SAMPLE_ICS_REQUEST, _mock_account())

            assert result is True
            event = cache_db.get_event_by_uid(conn, "invite-uid@example.com")
            assert event is not None
            assert event["source_email_message_id"] is None
        finally:
            conn.close()
            os.unlink(path)

    def test_uses_default_calendar(self):
        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.invite_processor import process_incoming_invite

        conn, path, _key = _create_temp_cache()
        try:
            plain_id = cache_db.upsert_calendar(
                conn, "cal-plain", "/cal-plain/", displayname="Plain"
            )
            default_id = cache_db.upsert_calendar(
                conn, "cal-default", "/cal-default/", displayname="Default", is_default=True
            )
            assert default_id != plain_id
            with patch(
                "app.modules.calendar.services.invite_processor._push_to_caldav",
                return_value=("/cal-default/invite-uid@example.com.ics", "etag-1"),
            ):
                result = process_incoming_invite(conn, SAMPLE_ICS_REQUEST, _mock_account())

            assert result is True
            event = cache_db.get_event_by_uid(conn, "invite-uid@example.com")
            assert event is not None
            assert event["calendar_id"] == default_id
        finally:
            conn.close()
            os.unlink(path)

    def test_skips_when_uid_already_imported(self):
        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.invite_processor import process_incoming_invite
        from app.shared.icalendar import generate_icalendar

        conn, path, _key = _create_temp_cache()
        try:
            cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Main")
            ical = generate_icalendar(
                {
                    "summary": "Team Meeting",
                    "dtstart": "20260615T100000Z",
                    "dtend": "20260615T110000Z",
                    "attendees": [
                        {
                            "cn": "Bob",
                            "email": "bob@example.com",
                            "partstat": "ACCEPTED",
                            "rsvp": "FALSE",
                        }
                    ],
                },
                uid="invite-uid@example.com",
            )
            cache_db.upsert_event(conn, "invite-uid@example.com", "/evt.ics", "e1", cal_id, ical)

            with patch(
                "app.modules.calendar.services.invite_processor._push_to_caldav"
            ) as mock_push:
                result = process_incoming_invite(conn, SAMPLE_ICS_REQUEST, _mock_account())

            assert result is False
            mock_push.assert_not_called()

            event = cache_db.get_event_by_uid(conn, "invite-uid@example.com")
            assert event is not None
            attendees = json.loads(event["attendees"])
            assert attendees[0]["partstat"] == "ACCEPTED"
        finally:
            conn.close()
            os.unlink(path)

    def test_skips_when_account_not_attendee(self):
        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.invite_processor import process_incoming_invite

        conn, path, _key = _create_temp_cache()
        try:
            cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Main")
            with patch(
                "app.modules.calendar.services.invite_processor._push_to_caldav"
            ) as mock_push:
                result = process_incoming_invite(
                    conn, SAMPLE_ICS_REQUEST_OTHER_ATTENDEE, _mock_account()
                )

            assert result is False
            mock_push.assert_not_called()
            assert cache_db.get_event_by_uid(conn, "invite-uid@example.com") is None
        finally:
            conn.close()
            os.unlink(path)

    def test_skips_when_no_calendars(self):
        from app.modules.calendar.services.invite_processor import process_incoming_invite

        conn, path, _key = _create_temp_cache()
        try:
            with patch(
                "app.modules.calendar.services.invite_processor._push_to_caldav"
            ) as mock_push:
                result = process_incoming_invite(conn, SAMPLE_ICS_REQUEST, _mock_account())

            assert result is False
            mock_push.assert_not_called()
        finally:
            conn.close()
            os.unlink(path)

    def test_ignores_non_request_methods(self):
        from app.modules.calendar.services.cache_db import upsert_calendar
        from app.modules.calendar.services.invite_processor import process_incoming_invite

        conn, path, _key = _create_temp_cache()
        try:
            upsert_calendar(conn, "cal-1", "/cal1/", displayname="Main")
            with patch(
                "app.modules.calendar.services.invite_processor._push_to_caldav"
            ) as mock_push:
                assert process_incoming_invite(conn, SAMPLE_ICS_REPLY, _mock_account()) is False
                assert process_incoming_invite(conn, SAMPLE_ICS_CANCEL, _mock_account()) is False
                assert process_incoming_invite(conn, "", _mock_account()) is False
            mock_push.assert_not_called()
        finally:
            conn.close()
            os.unlink(path)

    def test_push_failure_skips_cache_write(self):
        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.invite_processor import process_incoming_invite

        conn, path, _key = _create_temp_cache()
        try:
            cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Main")
            with patch(
                "app.modules.calendar.services.invite_processor._push_to_caldav",
                return_value=None,
            ):
                result = process_incoming_invite(conn, SAMPLE_ICS_REQUEST, _mock_account())

            assert result is False
            assert cache_db.get_event_by_uid(conn, "invite-uid@example.com") is None
        finally:
            conn.close()
            os.unlink(path)
