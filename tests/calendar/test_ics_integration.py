import json
from unittest.mock import patch, MagicMock

from app.shared.models.core import User, Domain, CustomerAccount
from app.shared.db import db


SAMPLE_ICS_REQUEST = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REQUEST\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:test-uid-123@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "LOCATION:Room 101\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;CN=Bob;ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SAMPLE_ICS_PUBLISH = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:PUBLISH\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:publish-uid-456@example.com\r\n"
    "SUMMARY:Company Holiday\r\n"
    "DTSTART;VALUE=DATE:20260704\r\n"
    "DTEND;VALUE=DATE:20260705\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SAMPLE_ICS_CANCEL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:CANCEL\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:test-uid-123@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "STATUS:CANCELLED\r\n"
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
    from app.modules.calendar.services import cache_db
    import tempfile, os

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        key = get_user_key(user_id)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        account.cache_db_path = path
        db.session.commit()

        conn = open_cache(path, key)
        return conn, path, key


class TestIcsParse:
    def test_parse_valid_request(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = client.post(
            "/app/calendar/api/ics-parse",
            data=json.dumps({"ical_text": SAMPLE_ICS_REQUEST}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["summary"] == "Team Meeting"
        assert data["method"] == "REQUEST"
        assert data["is_invitation"] is True
        assert data["is_cancellation"] is False
        assert data["uid"] == "test-uid-123@example.com"
        assert data["location"] == "Room 101"
        assert len(data["attendees"]) == 1
        assert data["attendees"][0]["email"] == "bob@example.com"

    def test_parse_empty(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-parse",
            data=json.dumps({"ical_text": ""}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_parse_publish(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-parse",
            data=json.dumps({"ical_text": SAMPLE_ICS_PUBLISH}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["method"] == "PUBLISH"
        assert data["is_publish"] is True
        assert data["is_invitation"] is False

    def test_parse_cancel(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-parse",
            data=json.dumps({"ical_text": SAMPLE_ICS_CANCEL}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_cancellation"] is True
        assert data["is_invitation"] is False


class TestIcsConflicts:
    def test_conflicts_no_params(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.get("/app/calendar/api/conflicts")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_conflicts_with_range_no_cache(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.get(
            "/app/calendar/api/conflicts?start=2026-06-15T09:00:00&end=2026-06-15T12:00:00"
        )
        assert resp.status_code == 200
        assert resp.get_json() == []


class TestIcsImport:
    def test_import_no_ical(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-import",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_import_no_calendar_id(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-import",
            data=json.dumps({"ical_text": SAMPLE_ICS_PUBLISH}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_import_success(self, app, authed_client):
        _setup_caldav_domain(app)
        client, user_id, account_id = authed_client

        from app.modules.calendar.services import cache_db
        import os

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cal_id = cache_db.upsert_calendar(conn, "cal-uid-1", "http://localhost:5232/user/cal1/", displayname="Test Cal")
        conn.close()

        with patch("app.modules.calendar.controllers.api.caldav") as mock_caldav, \
             patch("app.modules.calendar.controllers.api._get_credentials", return_value="pw"):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.create_event.return_value = (
                "http://localhost:5232/user/cal1/event1.ics",
                "etag-1",
            )

            resp = client.post(
                "/app/calendar/api/ics-import",
                data=json.dumps({
                    "ical_text": SAMPLE_ICS_PUBLISH,
                    "calendar_id": cal_id,
                    "source_email_message_id": 42,
                    "source_email_account_id": account_id,
                }),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["event_id"] is not None
        assert data["calendar_event_url"] is not None

        from app.modules.calendar.services.cache_db import open_cache
        conn = open_cache(path, key)
        event = cache_db.get_event(conn, data["event_id"])
        assert event is not None
        assert event["summary"] == "Company Holiday"
        assert event["source_email_message_id"] == 42
        assert event["source_email_account_id"] == account_id
        conn.close()
        os.unlink(path)


class TestIcsRsvp:
    def test_rsvp_no_ical(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-rsvp",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_rsvp_invalid_partstat(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-rsvp",
            data=json.dumps({"ical_text": SAMPLE_ICS_REQUEST, "calendar_id": 1, "partstat": "MAYBE"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_rsvp_accept(self, app, authed_client):
        _setup_caldav_domain(app)
        client, user_id, account_id = authed_client

        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.cache_db import open_cache
        import os

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cal_id = cache_db.upsert_calendar(conn, "cal-uid-1", "http://localhost:5232/user/cal1/", displayname="Test Cal")
        conn.close()

        with patch("app.modules.calendar.controllers.api.caldav") as mock_caldav, \
             patch("app.modules.calendar.controllers.api._get_credentials", return_value="pw"):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.create_event.return_value = (
                "http://localhost:5232/user/cal1/event-rsvp.ics",
                "etag-rsvp",
            )

            resp = client.post(
                "/app/calendar/api/ics-rsvp",
                data=json.dumps({
                    "ical_text": SAMPLE_ICS_REQUEST,
                    "calendar_id": cal_id,
                    "partstat": "ACCEPTED",
                    "source_email_message_id": 42,
                    "source_email_account_id": account_id,
                }),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["partstat"] == "ACCEPTED"
        assert data["event_id"] is not None

        conn = open_cache(path, key)
        event = cache_db.get_event(conn, data["event_id"])
        assert event is not None
        assert event["source_email_message_id"] == 42
        conn.close()
        os.unlink(path)


class TestIcsCancel:
    def test_cancel_no_ical(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post(
            "/app/calendar/api/ics-cancel",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_cancel_event_not_found(self, app, authed_client):
        client, user_id, account_id = authed_client

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        conn.close()

        resp = client.post(
            "/app/calendar/api/ics-cancel",
            data=json.dumps({"ical_text": SAMPLE_ICS_CANCEL}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["action"] == "not_found"
        import os
        os.unlink(path)

    def test_cancel_marks_event_cancelled(self, app, authed_client):
        client, user_id, account_id = authed_client

        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.cache_db import open_cache
        import os

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cal_id = cache_db.upsert_calendar(conn, "cal-uid-x", "http://localhost/x/", displayname="X")

        existing_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:test-uid-123@example.com\r\n"
            "SUMMARY:Team Meeting\r\n"
            "DTSTART:20260615T100000Z\r\nDTEND:20260615T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        event_id = cache_db.upsert_event(conn, "test-uid-123@example.com", "http://localhost/x/ev1.ics", "etag1", cal_id, existing_ics)
        conn.close()

        resp = client.post(
            "/app/calendar/api/ics-cancel",
            data=json.dumps({"ical_text": SAMPLE_ICS_CANCEL}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["action"] == "cancelled"
        assert data["event_id"] == event_id

        conn = open_cache(path, key)
        event = cache_db.get_event(conn, event_id)
        assert event["status"] == "CANCELLED"
        conn.close()
        os.unlink(path)


class TestEventPrefill:
    def test_event_new_with_prefill(self, app, authed_client):
        _setup_caldav_domain(app)
        client, user_id, account_id = authed_client

        from app.modules.calendar.services import cache_db
        import os

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cache_db.upsert_calendar(conn, "cal-uid-pf", "http://localhost/pf/", displayname="My Cal")
        conn.close()

        resp = client.get(
            "/app/calendar/events/new?summary=Test+Subject&description=Test+Body&attendee=alice%40example.com"
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Test Subject" in html
        assert "Test Body" in html
        assert "alice@example.com" in html
        os.unlink(path)


class TestEventDetailEmailLink:
    def test_event_detail_shows_email_link(self, app, authed_client):
        _setup_caldav_domain(app)
        client, user_id, account_id = authed_client

        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.cache_db import open_cache
        import os

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cal_id = cache_db.upsert_calendar(conn, "cal-uid-el", "http://localhost/el/", displayname="EL")

        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:el-test@example.com\r\n"
            "SUMMARY:Linked Event\r\nDTSTART:20260101T100000Z\r\nDTEND:20260101T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        event_id = cache_db.upsert_event(conn, "el-test@example.com", "http://localhost/el/ev.ics", "e1", cal_id, ics)
        cache_db.set_event_source_email(conn, event_id, 42, account_id)
        conn.close()

        resp = client.get(f"/app/calendar/events/{event_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "View original email" in html
        assert f"/app/mail/message/{account_id}/42" in html
        os.unlink(path)

    def test_event_detail_no_email_link(self, app, authed_client):
        _setup_caldav_domain(app)
        client, user_id, account_id = authed_client

        from app.modules.calendar.services import cache_db
        import os

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cal_id = cache_db.upsert_calendar(conn, "cal-uid-nl", "http://localhost/nl/", displayname="NL")

        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:nl-test@example.com\r\n"
            "SUMMARY:No Link Event\r\nDTSTART:20260101T100000Z\r\nDTEND:20260101T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        event_id = cache_db.upsert_event(conn, "nl-test@example.com", "http://localhost/nl/ev.ics", "e2", cal_id, ics)
        conn.close()

        resp = client.get(f"/app/calendar/events/{event_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "View original email" not in html
        os.unlink(path)


class TestConflicts:
    def test_conflict_detection(self, app, authed_client):
        client, user_id, account_id = authed_client

        from app.modules.calendar.services import cache_db
        from app.modules.calendar.services.cache_db import open_cache
        import os

        conn, path, key = _create_temp_cache(app, user_id, account_id)
        cal_id = cache_db.upsert_calendar(conn, "cal-uid-cf", "http://localhost/cf/", displayname="CF")

        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:cf-test@example.com\r\n"
            "SUMMARY:Existing Meeting\r\nDTSTART:20260615T100000Z\r\nDTEND:20260615T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        cache_db.upsert_event(conn, "cf-test@example.com", "http://localhost/cf/ev.ics", "e3", cal_id, ics)
        conn.close()

        resp = client.get(
            "/app/calendar/api/conflicts?start=2026-06-15T09:30:00&end=2026-06-15T10:30:00"
        )
        assert resp.status_code == 200
        conflicts = resp.get_json()
        assert len(conflicts) == 1
        assert conflicts[0]["summary"] == "Existing Meeting"

        resp2 = client.get(
            "/app/calendar/api/conflicts?start=2026-06-15T11:30:00&end=2026-06-15T12:00:00"
        )
        assert resp2.status_code == 200
        assert resp2.get_json() == []

        os.unlink(path)
