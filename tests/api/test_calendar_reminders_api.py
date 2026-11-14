"""Calendar reminders via the REST API (U12.30, U15.56, U15.57).

Repro coverage for the 2026-11-14 MCP issue log:
- #10 calendar_create_event: any reminders payload caused a CalDAV 400 —
  trigger_minutes must become a valid TRIGGER duration and types must be
  normalized/validated before CalDAV is touched.
- #11 calendar reads never echoed reminders back despite the documented
  "full event detail" contract.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.api.conftest import auth_header, cleanup_cache_db, create_api_token, setup_cache_db


@pytest.fixture()
def calendar_api(app, api_customer):
    from app.modules.calendar.services.cache import get_cache_path as _cal_path

    client, user_id, account_id = api_customer
    with app.app_context():
        token_value, _ = create_api_token(app, user_id)
    cache_path = setup_cache_db(app, account_id, cache_path_fn=_cal_path)
    yield client, token_value, account_id, cache_path
    cleanup_cache_db(cache_path)


def _seed_calendar(cache_path):
    from app.modules.calendar.services.cache_db import open_cache, upsert_calendar

    conn = open_cache(cache_path, "a" * 64)
    cal_id = upsert_calendar(
        conn,
        uid="cal-001",
        href="/caldav/cal-001/",
        displayname="Personal",
        color="#4285f4",
        is_default=True,
    )
    conn.close()
    return cal_id


def _caldav_patches():
    """Mock the CalDAV session + create/update so tests never touch a server."""
    mock_session = MagicMock()
    create_event = patch(
        "app.modules.calendar.services.caldav.create_event",
        return_value=("/caldav/cal-001/new.ics", "etag-new"),
    )
    update_event = patch(
        "app.modules.calendar.services.caldav.update_event", return_value="etag-new"
    )
    return mock_session, create_event, update_event


class TestCreateEventReminders:
    def test_reminders_lowercase_display_accepted(self, app, calendar_api):
        """Issue #10 repro: lowercase 'display' + integer trigger_minutes must
        produce a valid VALARM, not a CalDAV 400."""
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        mock_session, ce, _ue = _caldav_patches()
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, None, None, None),
            ),
            ce as mock_create,
        ):
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    "summary": "Reminder event",
                    "start": "2026-06-01T10:00:00+00:00",
                    "end": "2026-06-01T11:00:00+00:00",
                    "reminders": [{"type": "display", "trigger_minutes": 15}],
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201, resp.data
        # The PUT body carries a valid TRIGGER duration, not the raw integer
        ical_text = mock_create.call_args[0][2]
        assert "TRIGGER:-PT15M" in ical_text
        assert "ACTION:DISPLAY" in ical_text

    def test_reminders_invalid_type_rejected_before_caldav(self, app, calendar_api):
        """Issue #10 repro: 'notification'/'popup' are loud VALIDATION_ERRORs —
        the CalDAV server is never touched."""
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        for bad_type in ("notification", "popup", "sms"):
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    "summary": "Bad reminder",
                    "start": "2026-06-01T10:00:00+00:00",
                    "end": "2026-06-01T11:00:00+00:00",
                    "reminders": [{"type": bad_type, "trigger_minutes": 15}],
                },
                headers=auth_header(token),
            )
            assert resp.status_code == 400, bad_type
            assert json.loads(resp.data)["error"]["code"] == "VALIDATION_ERROR"

    def test_reminders_non_integer_trigger_rejected(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        resp = client.post(
            "/api/v1/calendar/events",
            json={
                "calendar_id": cal_id,
                "summary": "Bad trigger",
                "start": "2026-06-01T10:00:00+00:00",
                "end": "2026-06-01T11:00:00+00:00",
                "reminders": [{"type": "DISPLAY", "trigger_minutes": "tomorrow"}],
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 400
        assert json.loads(resp.data)["error"]["code"] == "VALIDATION_ERROR"

    def test_reminders_hours_and_days_durations(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        mock_session, ce, _ue = _caldav_patches()
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, None, None, None),
            ),
            ce as mock_create,
        ):
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    "summary": "Long reminder",
                    "start": "2026-06-01T10:00:00+00:00",
                    "end": "2026-06-01T11:00:00+00:00",
                    "reminders": [
                        {"type": "EMAIL", "trigger_minutes": 60},
                        {"type": "DISPLAY", "trigger_minutes": 1440},
                    ],
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201, resp.data
        ical_text = mock_create.call_args[0][2]
        assert "TRIGGER:-PT1H" in ical_text
        assert "TRIGGER:-P1D" in ical_text


class TestEventReadsEchoReminders:
    def test_get_event_returns_reminders(self, app, calendar_api):
        """Issue #11 repro: reads echo reminders back as {type, trigger_minutes}."""
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        from app.modules.calendar.services.cache_db import open_cache, upsert_event

        conn = open_cache(cache_path, "a" * 64)
        upsert_event(
            conn,
            uid="event-r1",
            href="/caldav/cal-001/event-r1.ics",
            etag="etag-r1",
            calendar_id=cal_id,
            ical_text=(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                "BEGIN:VEVENT\r\n"
                "UID:event-r1\r\n"
                "DTSTART:20260601T100000Z\r\n"
                "DTEND:20260601T110000Z\r\n"
                "SUMMARY:Meeting with reminders\r\n"
                "BEGIN:VALARM\r\n"
                "TRIGGER:-PT30M\r\n"
                "ACTION:DISPLAY\r\n"
                "DESCRIPTION:Meeting with reminders\r\n"
                "END:VALARM\r\n"
                "BEGIN:VALARM\r\n"
                "TRIGGER:-PT1H\r\n"
                "ACTION:EMAIL\r\n"
                "DESCRIPTION:Meeting with reminders\r\n"
                "END:VALARM\r\n"
                "END:VEVENT\r\n"
                "END:VCALENDAR\r\n"
            ),
        )
        row = conn.execute("SELECT id FROM calendar_events WHERE uid = 'event-r1'").fetchone()
        assert row is not None
        row_id = row["id"]
        conn.close()

        resp = client.get(
            f"/api/v1/calendar/events/{row_id}",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        reminders = data["reminders"]
        assert {"type": "DISPLAY", "trigger_minutes": 30} in reminders
        assert {"type": "EMAIL", "trigger_minutes": 60} in reminders

    def test_list_events_include_reminders(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        from app.modules.calendar.services.cache_db import open_cache, upsert_event

        conn = open_cache(cache_path, "a" * 64)
        upsert_event(
            conn,
            uid="event-r2",
            href="/caldav/cal-001/event-r2.ics",
            etag="etag-r2",
            calendar_id=cal_id,
            ical_text=(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                "BEGIN:VEVENT\r\nUID:event-r2\r\n"
                "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
                "SUMMARY:Listed with reminder\r\n"
                "BEGIN:VALARM\r\nTRIGGER:-PT15M\r\nACTION:DISPLAY\r\n"
                "END:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            ),
        )
        conn.close()
        resp = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        target = next(e for e in items if e.get("uid") == "event-r2")
        assert target["reminders"] == [{"type": "DISPLAY", "trigger_minutes": 15}]


class TestUpdateEventReminders:
    def test_update_applies_replacement_reminders(self, app, calendar_api):
        """The update path previously ignored body.reminders entirely (phantom
        success — part of issue #10's confusing workaround)."""
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        from app.modules.calendar.services.cache_db import open_cache, upsert_event

        conn = open_cache(cache_path, "a" * 64)
        upsert_event(
            conn,
            uid="event-u1",
            href="/caldav/cal-001/event-u1.ics",
            etag="etag-u1",
            calendar_id=cal_id,
            ical_text=(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:event-u1\r\n"
                "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
                "SUMMARY:Update me\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            ),
        )
        row = conn.execute("SELECT id FROM calendar_events WHERE uid = 'event-u1'").fetchone()
        assert row is not None
        row_id = row["id"]
        conn.close()

        mock_session, _ce, ue = _caldav_patches()
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, None, None, None),
            ),
            ue as mock_update,
        ):
            resp = client.put(
                f"/api/v1/calendar/events/{row_id}",
                json={
                    "reminders": [
                        {"type": "DISPLAY", "trigger_minutes": 10},
                        {"type": "DISPLAY", "trigger_minutes": 30},
                    ]
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 200, resp.data
        ical_text = mock_update.call_args[0][2]
        assert "TRIGGER:-PT10M" in ical_text
        assert "TRIGGER:-PT30M" in ical_text
