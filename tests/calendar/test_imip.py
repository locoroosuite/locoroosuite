import base64
import json
import os
import tempfile
from unittest.mock import patch, MagicMock

from app.shared.models.core import Domain, CustomerAccount
from app.shared.db import db


SAMPLE_ICS_REQUEST = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REQUEST\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:test-imip-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "LOCATION:Room 101\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;CN=Test User;ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:test@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _setup_caldav_domain(app):
    with app.app_context():
        domain = Domain.query.first()
        domain.caldav_host = "localhost"
        domain.caldav_port = 5232
        domain.caldav_use_tls = False
        db.session.commit()


def _create_temp_cache(app, user_id, account_id):
    from app.shared.keys import get_user_key
    from app.modules.calendar.services.cache_db import open_cache

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        key = get_user_key(user_id)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        account.cache_db_path = path
        db.session.commit()

        conn = open_cache(path, key)
        return conn, path, key


class TestBuildImipEmail:
    def test_build_request_email(self):
        from app.modules.calendar.services.imip import build_imip_email
        event_data = {
            "summary": "Test Event",
            "dtstart": "20260615T100000Z",
            "dtend": "20260615T110000Z",
            "location": "Room A",
            "uid": "test-123",
            "organizer": {"cn": "Alice", "email": "alice@example.com"},
            "attendees": [{"cn": "Bob", "email": "bob@example.com"}],
        }
        msg_bytes, subject = build_imip_email(
            "alice@example.com", "Alice",
            [{"email": "bob@example.com"}],
            event_data, "REQUEST", uid="test-123",
        )
        msg_str = msg_bytes.decode("utf-8", errors="replace")
        assert "Invitation: Test Event" in subject
        assert "bob@example.com" in msg_str
        assert "text/calendar" in msg_str
        assert "method=REQUEST" in msg_str
        assert "invite.ics" in msg_str

    def test_build_request_email_formats_datetime_with_timezone(self):
        import email as email_lib
        from app.modules.calendar.services.imip import build_imip_email
        event_data = {
            "summary": "Testing Test with Invite",
            "dtstart": "2026-05-14T12:00:00",
            "dtend": "2026-05-14T13:00:00",
            "timezone": "Australia/Adelaide",
            "uid": "test-tz-1",
            "organizer": {"cn": "Alice", "email": "alice@example.com"},
            "attendees": [{"cn": "Bob", "email": "bob@example.com"}],
        }
        msg_bytes, subject = build_imip_email(
            "alice@example.com", "Alice",
            [{"email": "bob@example.com"}],
            event_data, "REQUEST", uid="test-tz-1",
        )
        msg = email_lib.message_from_bytes(msg_bytes)
        body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8")
                break
        assert "When:" in body
        assert "12:00 PM" in body
        assert "01:00 PM" in body
        assert "Australia/Adelaide" in body

    def test_build_request_email_utc_datetimes(self):
        import email as email_lib
        from app.modules.calendar.services.imip import build_imip_email
        event_data = {
            "summary": "UTC Event",
            "dtstart": "20260615T100000Z",
            "dtend": "20260615T110000Z",
            "uid": "test-utc-1",
            "organizer": {"cn": "Alice", "email": "alice@example.com"},
            "attendees": [{"cn": "Bob", "email": "bob@example.com"}],
        }
        msg_bytes, _ = build_imip_email(
            "alice@example.com", "Alice",
            [{"email": "bob@example.com"}],
            event_data, "REQUEST", uid="test-utc-1",
        )
        msg = email_lib.message_from_bytes(msg_bytes)
        body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8")
                break
        assert "10:00 AM" in body
        assert "UTC" in body

    def test_build_cancel_email(self):
        from app.modules.calendar.services.imip import build_imip_email
        event_data = {
            "summary": "Test Event",
            "uid": "test-123",
        }
        msg_bytes, subject = build_imip_email(
            "alice@example.com", "Alice",
            [{"email": "bob@example.com"}],
            event_data, "CANCEL", uid="test-123",
        )
        msg_str = msg_bytes.decode("utf-8", errors="replace")
        assert "Cancelled: Test Event" in subject
        assert "cancelled" in msg_str.lower()

    def test_build_reply_email(self):
        from app.modules.calendar.services.imip import build_imip_email
        event_data = {
            "summary": "Test Event",
            "uid": "test-123",
            "reply_attendee": {"cn": "Bob", "email": "bob@example.com"},
            "reply_partstat": "ACCEPTED",
            "organizer": {"cn": "Alice", "email": "alice@example.com"},
        }
        msg_bytes, subject = build_imip_email(
            "bob@example.com", "Bob", [],
            event_data, "REPLY", uid="test-123",
        )
        msg_str = msg_bytes.decode("utf-8", errors="replace")
        assert "Re: Test Event" in subject
        assert "alice@example.com" in msg_str
        assert "method=REPLY" in msg_str


class TestSendInviteApi:
    def test_send_invite_no_event_id(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/send-invite",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_send_invite_invalid_method(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/send-invite",
            data=json.dumps({"event_id": 1, "method": "BAD"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_send_invite_event_not_found(self, app, authed_client):
        client, user_id, account_id = authed_client
        _setup_caldav_domain(app)
        conn, path, key = _create_temp_cache(app, user_id, account_id)
        conn.close()
        try:
            resp = client.post(
                "/app/calendar/api/send-invite",
                data=json.dumps({"event_id": 99999, "method": "REQUEST"}),
                content_type="application/json",
            )
            assert resp.status_code == 404
        finally:
            os.unlink(path)

    def test_send_invite_no_attendees(self, app, authed_client):
        client, user_id, account_id = authed_client
        _setup_caldav_domain(app)
        conn, path, key = _create_temp_cache(app, user_id, account_id)
        from app.modules.calendar.services import cache_db
        cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")
        from app.shared.icalendar import generate_icalendar
        ical = generate_icalendar({"summary": "No Attendees", "dtstart": "20260615T100000Z"})
        uid = "no-att-uid"
        event_id = cache_db.upsert_event(conn, uid, "/evt.ics", "e1", cal_id, ical)
        conn.close()

        try:
            resp = client.post(
                "/app/calendar/api/send-invite",
                data=json.dumps({"event_id": event_id, "method": "REQUEST"}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "No attendees" in data.get("message", "")
        finally:
            os.unlink(path)

    def test_send_invite_success(self, app, authed_client):
        client, user_id, account_id = authed_client
        _setup_caldav_domain(app)
        conn, path, key = _create_temp_cache(app, user_id, account_id)
        from app.modules.calendar.services import cache_db
        cal_id = cache_db.upsert_calendar(conn, "cal-1", "/cal1/", displayname="Test")

        ical_data = {
            "summary": "Team Meeting",
            "dtstart": "20260615T100000Z",
            "dtend": "20260615T110000Z",
            "organizer": {"cn": "Alice", "email": "test@example.com"},
            "attendees": [{"cn": "Bob", "email": "bob@example.com", "role": "REQ-PARTICIPANT", "partstat": "NEEDS-ACTION", "rsvp": "TRUE"}],
        }
        from app.shared.icalendar import generate_icalendar
        ical = generate_icalendar(ical_data, uid="imip-test-uid")
        event_id = cache_db.upsert_event(conn, "imip-test-uid", "/evt.ics", "e1", cal_id, ical)
        conn.close()

        try:
            with app.app_context():
                account = db.session.get(CustomerAccount, account_id)
                from cryptography.fernet import Fernet
                key_bytes = bytes.fromhex("0" * 64)
                fernet_key = base64.urlsafe_b64encode(key_bytes)
                f = Fernet(fernet_key)
                account.encrypted_secret = f.encrypt(b"test-password")
                db.session.commit()

            with patch("app.modules.mail.services.smtp_client.smtp_connect") as mock_smtp, \
                 patch("app.modules.mail.services.smtp_client.smtp_login"), \
                 patch("app.modules.mail.services.smtp_client.smtp_send") as mock_send:
                mock_smtp.return_value = MagicMock()
                resp = client.post(
                    "/app/calendar/api/send-invite",
                    data=json.dumps({"event_id": event_id, "method": "REQUEST"}),
                    content_type="application/json",
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            recipients = call_args[0][2]
            assert "bob@example.com" in recipients
        finally:
            os.unlink(path)


class TestRsvpReplySending:
    def test_rsvp_sends_reply_email(self, app, authed_client):
        _setup_caldav_domain(app)
        client, user_id, account_id = authed_client
        from app.modules.calendar.services import cache_db

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cal_id = cache_db.upsert_calendar(conn, "cal-uid-1", "http://localhost:5232/user/cal1/", displayname="Test Cal")
        conn.close()

        try:
            with app.app_context():
                account = db.session.get(CustomerAccount, account_id)
                from cryptography.fernet import Fernet
                key_bytes = bytes.fromhex("0" * 64)
                fernet_key = base64.urlsafe_b64encode(key_bytes)
                f = Fernet(fernet_key)
                account.encrypted_secret = f.encrypt(b"test-password")
                db.session.commit()

            with patch("app.modules.calendar.controllers.api.caldav") as mock_caldav, \
                 patch("app.modules.calendar.controllers.api._get_credentials", return_value="pw"), \
                 patch("app.modules.mail.services.smtp_client.smtp_connect") as mock_smtp, \
                 patch("app.modules.mail.services.smtp_client.smtp_login"), \
                 patch("app.modules.mail.services.smtp_client.smtp_send") as mock_send:
                mock_session = MagicMock()
                mock_caldav.discover_calendars.return_value = (mock_session, [])
                mock_caldav.create_event.return_value = (
                    "http://localhost:5232/user/cal1/event-rsvp.ics",
                    "etag-rsvp",
                )
                mock_smtp.return_value = MagicMock()

                resp = client.post(
                    "/app/calendar/api/ics-rsvp",
                    data=json.dumps({
                        "ical_text": SAMPLE_ICS_REQUEST,
                        "calendar_id": cal_id,
                        "partstat": "ACCEPTED",
                    }),
                    content_type="application/json",
                )

            assert resp.status_code == 200
            data = resp.get_json()
            assert data["partstat"] == "ACCEPTED"
            mock_send.assert_called_once()
        finally:
            os.unlink(path)


class TestDeleteWithNotification:
    def test_delete_with_attendees_shows_modal_button(self, app, authed_client):
        client, user_id, account_id = authed_client
        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            domain = Domain.query.first()
            domain.caldav_host = "localhost"
            domain.caldav_port = 5232
            domain.caldav_use_tls = False
            db.session.commit()

            account = db.session.get(CustomerAccount, account_id)
            key = get_user_key(user_id)
            fd, path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            account.cache_db_path = path
            db.session.commit()

            conn = open_cache(path, key)
            conn.execute(
                "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
                ("cal-1", "/test/cal1/", "Work", "#4285f4"),
            )
            attendees_json = json.dumps([{"cn": "Bob", "email": "bob@example.com"}])
            conn.execute(
                "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, raw_ical, attendees) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("evt-del", "/test/evt-del.ics", "etag-del", 1, "Delete Me", "2025-01-15T09:00:00+00:00",
                 "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Delete Me\r\nEND:VEVENT\r\nEND:VCALENDAR",
                 attendees_json),
            )
            conn.commit()
            event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-del'").fetchone()[0]
            conn.close()

        try:
            resp = client.get(f"/app/calendar/events/{event_id}")
            assert resp.status_code == 200
            assert b"delete-btn" in resp.data
            assert b"delete-modal" in resp.data
            assert b"Delete &amp; notify guests" in resp.data
        finally:
            os.unlink(path)

    def test_delete_without_notification(self, app, authed_client):
        client, user_id, account_id = authed_client
        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            domain = Domain.query.first()
            domain.caldav_host = "localhost"
            domain.caldav_port = 5232
            domain.caldav_use_tls = False
            db.session.commit()

            account = db.session.get(CustomerAccount, account_id)
            key = get_user_key(user_id)
            fd, path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            account.cache_db_path = path
            db.session.commit()

            conn = open_cache(path, key)
            conn.execute(
                "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
                ("cal-1", "/test/cal1/", "Work", "#4285f4"),
            )
            attendees_json = json.dumps([{"cn": "Bob", "email": "bob@example.com"}])
            conn.execute(
                "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, raw_ical, attendees) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("evt-del2", "/test/evt-del2.ics", "etag-del2", 1, "Delete Me", "2025-01-15T09:00:00+00:00",
                 "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Delete Me\r\nEND:VEVENT\r\nEND:VCALENDAR",
                 attendees_json),
            )
            conn.commit()
            event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-del2'").fetchone()[0]
            conn.close()

        try:
            with patch("app.modules.calendar.controllers.events.caldav") as mock_caldav:
                mock_session = MagicMock()
                mock_caldav.discover_calendars.return_value = (mock_session, [])
                mock_caldav.delete_event.return_value = True
                resp = client.post(f"/app/calendar/events/{event_id}/delete")
            assert resp.status_code == 302
        finally:
            os.unlink(path)

    def test_delete_with_notification_sends_cancel(self, app, authed_client):
        client, user_id, account_id = authed_client
        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            domain = Domain.query.first()
            domain.caldav_host = "localhost"
            domain.caldav_port = 5232
            domain.caldav_use_tls = False
            db.session.commit()

            account = db.session.get(CustomerAccount, account_id)
            key = get_user_key(user_id)
            fd, path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            account.cache_db_path = path
            db.session.commit()

            conn = open_cache(path, key)
            conn.execute(
                "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
                ("cal-1", "/test/cal1/", "Work", "#4285f4"),
            )
            attendees_json = json.dumps([{"cn": "Bob", "email": "bob@example.com"}])
            conn.execute(
                "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, raw_ical, attendees) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("evt-del3", "/test/evt-del3.ics", "etag-del3", 1, "Delete Me", "2025-01-15T09:00:00+00:00",
                 "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Delete Me\r\nEND:VEVENT\r\nEND:VCALENDAR",
                 attendees_json),
            )
            conn.commit()
            event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-del3'").fetchone()[0]
            conn.close()

        try:
            with app.app_context():
                account = db.session.get(CustomerAccount, account_id)
                from cryptography.fernet import Fernet
                key_bytes = bytes.fromhex("0" * 64)
                fernet_key = base64.urlsafe_b64encode(key_bytes)
                f = Fernet(fernet_key)
                account.encrypted_secret = f.encrypt(b"test-password")
                db.session.commit()

            with patch("app.modules.calendar.controllers.events.caldav") as mock_caldav, \
                 patch("app.modules.mail.services.smtp_client.smtp_connect") as mock_smtp, \
                 patch("app.modules.mail.services.smtp_client.smtp_login"), \
                 patch("app.modules.mail.services.smtp_client.smtp_send") as mock_send:
                mock_session = MagicMock()
                mock_caldav.discover_calendars.return_value = (mock_session, [])
                mock_caldav.delete_event.return_value = True
                mock_smtp.return_value = MagicMock()
                resp = client.post(f"/app/calendar/events/{event_id}/delete", data={"send_notification": "1"})
            assert resp.status_code == 302
            mock_send.assert_called_once()
        finally:
            os.unlink(path)


class TestFormatImipDatetime:
    def test_format_naive_datetime_with_event_tz(self):
        from app.modules.calendar.services.imip import _format_imip_datetime
        formatted, tz_label = _format_imip_datetime("2026-05-14T12:00:00", "Australia/Adelaide")
        assert "12:00 PM" in formatted
        assert tz_label == "Australia/Adelaide"

    def test_format_naive_datetime_without_tz(self):
        from app.modules.calendar.services.imip import _format_imip_datetime
        formatted, tz_label = _format_imip_datetime("2026-05-14T12:00:00")
        assert "12:00 PM" in formatted
        assert tz_label == "UTC"

    def test_format_utc_datetime(self):
        from app.modules.calendar.services.imip import _format_imip_datetime
        formatted, tz_label = _format_imip_datetime("20260615T100000Z")
        assert "10:00 AM" in formatted
        assert tz_label == "UTC"

    def test_format_empty_string(self):
        from app.modules.calendar.services.imip import _format_imip_datetime
        formatted, tz_label = _format_imip_datetime("")
        assert formatted == ""
        assert tz_label == ""

    def test_format_same_day_when(self):
        from app.modules.calendar.services.imip import _format_imip_when
        result = _format_imip_when("2026-05-14T12:00:00", "2026-05-14T13:00:00", "Australia/Adelaide")
        assert "12:00 PM" in result
        assert "01:00 PM" in result
        assert "Australia/Adelaide" in result
        assert result.count("May 14") == 1

    def test_format_multi_day_when(self):
        from app.modules.calendar.services.imip import _format_imip_when
        result = _format_imip_when("2026-05-14T12:00:00", "2026-05-15T13:00:00", "Australia/Adelaide")
        assert "May 14" in result
        assert "May 15" in result
        assert "Australia/Adelaide" in result

    def test_format_no_end(self):
        from app.modules.calendar.services.imip import _format_imip_when
        result = _format_imip_when("2026-05-14T12:00:00", "", "Australia/Adelaide")
        assert "12:00 PM" in result
        assert "Australia/Adelaide" in result

    def test_format_no_start(self):
        from app.modules.calendar.services.imip import _format_imip_when
        result = _format_imip_when("", "2026-05-14T13:00:00", "Australia/Adelaide")
        assert result == ""


class TestSendUpdatesModal:
    def test_detail_with_send_updates_shows_modal(self, app, authed_client):
        client, user_id, account_id = authed_client
        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            domain = Domain.query.first()
            domain.caldav_host = "localhost"
            domain.caldav_port = 5232
            domain.caldav_use_tls = False
            db.session.commit()

            account = db.session.get(CustomerAccount, account_id)
            key = get_user_key(user_id)
            fd, path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            account.cache_db_path = path
            db.session.commit()

            conn = open_cache(path, key)
            conn.execute(
                "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
                ("cal-1", "/test/cal1/", "Work", "#4285f4"),
            )
            attendees_json = json.dumps([{"cn": "Bob", "email": "bob@example.com"}])
            conn.execute(
                "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, raw_ical, attendees) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("evt-modal", "/test/evt-modal.ics", "etag-modal", 1, "Modal Test", "2025-01-15T09:00:00+00:00",
                 "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Modal Test\r\nEND:VEVENT\r\nEND:VCALENDAR",
                 attendees_json),
            )
            conn.commit()
            event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-modal'").fetchone()[0]
            conn.close()

        try:
            resp = client.get(f"/app/calendar/events/{event_id}?send_updates=1")
            assert resp.status_code == 200
            assert b"send-updates-modal" in resp.data
            assert b"Send updates to guests?" in resp.data
        finally:
            os.unlink(path)
