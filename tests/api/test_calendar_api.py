import json

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


def _seed_calendar_cache(cache_path, dek="a" * 64):
    from app.modules.calendar.services.cache_db import open_cache, upsert_calendar, upsert_event

    conn = open_cache(cache_path, dek)
    cal_id = upsert_calendar(
        conn,
        uid="cal-001",
        href="/caldav/cal-001/",
        displayname="Personal",
        color="#4285f4",
        is_default=True,
    )
    upsert_event(
        conn,
        uid="event-001",
        href="/caldav/cal-001/event-001.ics",
        etag="etag-1",
        calendar_id=cal_id,
        ical_text=(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:event-001\r\n"
            "DTSTART:20260601T100000Z\r\n"
            "DTEND:20260601T110000Z\r\n"
            "SUMMARY:Team Meeting\r\n"
            "DESCRIPTION:Weekly sync\r\n"
            "LOCATION:Room 101\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ),
    )
    upsert_event(
        conn,
        uid="event-002",
        href="/caldav/cal-001/event-002.ics",
        etag="etag-2",
        calendar_id=cal_id,
        ical_text=(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:event-002\r\n"
            "DTSTART:20260615T090000Z\r\n"
            "DTEND:20260615T100000Z\r\n"
            "SUMMARY:Project Review\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ),
    )
    conn.close()
    return cal_id


class TestListCalendars:
    def test_empty_list(self, app, calendar_api):
        client, token, _account_id, _ = calendar_api
        resp = client.get("/api/v1/calendar/calendars", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_returns_seeded_calendars(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        resp = client.get("/api/v1/calendar/calendars", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        cal = data["data"][0]
        assert cal["name"] == "Personal"
        assert cal["color"] == "#4285f4"
        assert cal["is_default"] is True

    def test_calendar_has_expected_fields(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        resp = client.get("/api/v1/calendar/calendars", headers=auth_header(token))
        data = json.loads(resp.data)
        cal = data["data"][0]
        for key in ("id", "uid", "name", "color", "is_default"):
            assert key in cal, f"Missing field: {key}"


class TestListEvents:
    def test_calendar_not_found(self, app, calendar_api):
        client, token, _account_id, _ = calendar_api
        resp = client.get("/api/v1/calendar/calendars/99999/events", headers=auth_header(token))
        assert resp.status_code == 404

    def test_returns_events_for_calendar(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar_cache(cache_path)
        resp = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events?since=2026-05-01T00:00:00Z&until=2026-07-01T00:00:00Z",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        summaries = {e["summary"] for e in data["data"]}
        assert "Team Meeting" in summaries
        assert "Project Review" in summaries

    def test_event_has_expected_fields(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar_cache(cache_path)
        resp = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events?since=2026-05-01T00:00:00Z&until=2026-07-01T00:00:00Z",
            headers=auth_header(token),
        )
        data = json.loads(resp.data)
        event = data["data"][0]
        for key in (
            "id",
            "uid",
            "summary",
            "description",
            "location",
            "start",
            "end",
            "is_all_day",
            "calendar_id",
        ):
            assert key in event, f"Missing field: {key}"

    def test_events_with_date_range(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar_cache(cache_path)
        resp = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events?since=2026-06-01T00:00:00Z&until=2026-06-02T00:00:00Z",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        assert data["data"][0]["summary"] == "Team Meeting"


class TestGetEvent:
    def test_not_found(self, app, calendar_api):
        client, token, _account_id, _ = calendar_api
        resp = client.get("/api/v1/calendar/events/99999", headers=auth_header(token))
        assert resp.status_code == 404

    def test_returns_event_detail(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        list_resp = client.get("/api/v1/calendar/search?q=Team+Meeting", headers=auth_header(token))
        event_id = json.loads(list_resp.data)["data"][0]["id"]

        resp = client.get(f"/api/v1/calendar/events/{event_id}", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["summary"] == "Team Meeting"
        assert data["location"] == "Room 101"


class TestSearchEvents:
    def test_missing_query_returns_422(self, app, calendar_api):
        client, token, _account_id, _ = calendar_api
        resp = client.get("/api/v1/calendar/search", headers=auth_header(token))
        assert resp.status_code == 422

    def test_search_returns_matching(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        resp = client.get("/api/v1/calendar/search?q=Meeting", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        assert data["data"][0]["summary"] == "Team Meeting"

    def test_search_no_results(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        resp = client.get("/api/v1/calendar/search?q=nonexistent", headers=auth_header(token))
        assert resp.status_code == 200
        assert json.loads(resp.data)["data"] == []


class TestFreeBusy:
    def test_missing_params_returns_422(self, app, calendar_api):
        client, token, _account_id, _ = calendar_api
        resp = client.post(
            "/api/v1/calendar/free-busy",
            json={},
            headers=auth_header(token),
        )
        assert resp.status_code == 422

    def test_returns_busy_periods(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        resp = client.post(
            "/api/v1/calendar/free-busy",
            json={"start": "2026-06-01T00:00:00Z", "end": "2026-06-30T00:00:00Z"},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1
        assert "start" in data["data"][0]
        assert "end" in data["data"][0]


class TestDeleteCalendar:
    def test_not_found(self, app, calendar_api):
        client, token, _account_id, _ = calendar_api
        resp = client.delete(
            "/api/v1/calendar/calendars/99999",
            json={"confirm": True},
            headers=auth_header(token),
        )
        assert resp.status_code == 404

    def test_missing_confirm_returns_400(self, app, calendar_api):
        client, token, _account_id, _ = calendar_api
        resp = client.delete(
            "/api/v1/calendar/calendars/1",
            json={"confirm": False},
            headers=auth_header(token),
        )
        assert resp.status_code in (400, 429)


class TestUpdateEvent:
    def test_update_event_uses_raw_ical_column(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        list_resp = client.get("/api/v1/calendar/search?q=Team+Meeting", headers=auth_header(token))
        event_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_session = MagicMock()
        mock_session.put.return_value = MagicMock(status_code=204, headers={})
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
        ):
            resp = client.put(
                f"/api/v1/calendar/events/{event_id}",
                json={"summary": "Updated Meeting", "location": "Room 202"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["summary"] == "Updated Meeting"
        put_args = mock_session.put.call_args
        body = put_args[1]["data"] if "data" in put_args[1] else put_args[0][0]
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        assert "DTSTART" in body
        assert "DTEND" in body

    def test_update_event_refreshes_etag_and_escapes_description(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        list_resp = client.get("/api/v1/calendar/search?q=Team+Meeting", headers=auth_header(token))
        event_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_session = MagicMock()
        mock_session.put.side_effect = [
            MagicMock(status_code=204, headers={"ETag": "etag-updated-1"}),
            MagicMock(status_code=204, headers={"ETag": "etag-updated-2"}),
        ]
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
        ):
            resp = client.put(
                f"/api/v1/calendar/events/{event_id}",
                json={"location": "Microsoft Teams"},
                headers=auth_header(token),
            )
            assert resp.status_code == 200
            rich_description = (
                "Recruitment conversation (a&co Partners)\n\n"
                "Teams join: https://teams.microsoft.com/meet/450750393784079?p=qqdbYs1PzmBEDZYrhK\n"
                "Passcode: gK2Sn3Ys\n+61 425 275 210"
            )
            resp = client.put(
                f"/api/v1/calendar/events/{event_id}",
                json={"description": rich_description},
                headers=auth_header(token),
            )
            assert resp.status_code == 200
            data = json.loads(resp.data)["data"]
            assert data["description"] == rich_description

        first_put, second_put = mock_session.put.call_args_list
        # First PUT uses the etag seeded in the cache
        assert first_put[1]["headers"]["If-Match"] == "etag-1"
        # Second PUT must use the etag returned by the first response — the
        # regression for the 412 Precondition Failed on follow-up updates
        assert second_put[1]["headers"]["If-Match"] == "etag-updated-1"

        body = second_put[1]["data"].decode("utf-8")
        for line in body.split("\r\n"):
            assert "\n" not in line
            assert len(line.encode("utf-8")) <= 75, line
        assert (
            "DESCRIPTION:Recruitment conversation (a&co Partners)\\n\\nTeams join:"
            in body.replace("\r\n ", "")
        )


class TestTimezoneConversion:
    def test_format_dt_converts_to_utc(self):
        from app.shared.icalendar import _format_dt

        result = _format_dt("2026-05-25T10:00:00+09:30", utc=True)
        assert result == "20260525T003000Z"

    def test_format_dt_naive_keeps_time(self):
        from app.shared.icalendar import _format_dt

        result = _format_dt("2026-05-25T10:00:00", utc=True)
        assert result == "20260525T100000Z"

    def test_format_dt_utc_stays_same(self):
        from app.shared.icalendar import _format_dt

        result = _format_dt("2026-05-25T10:00:00+00:00", utc=True)
        assert result == "20260525T100000Z"

    def test_format_dt_non_utc(self):
        from app.shared.icalendar import _format_dt

        result = _format_dt("2026-05-25T10:00:00+09:30", utc=False)
        assert result == "20260525T100000"


class TestListEventsNoDateRange:
    def test_list_events_without_date_range(self, app, calendar_api):
        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar_cache(cache_path)
        resp = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        summaries = {e["summary"] for e in data["data"]}
        assert "Team Meeting" in summaries
        assert "Project Review" in summaries

    def test_list_events_empty_calendar(self, app, calendar_api):
        from app.modules.calendar.services.cache_db import open_cache, upsert_calendar

        client, token, _account_id, cache_path = calendar_api
        conn = open_cache(cache_path, "a" * 64)
        cal_id = upsert_calendar(conn, uid="empty-cal", href="/caldav/empty/", displayname="Empty")
        conn.close()
        resp = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []


class TestFreeBusyCalendarFilter:
    def test_calendar_ids_filter(self, app, calendar_api):
        from app.modules.calendar.services.cache_db import open_cache, upsert_calendar, upsert_event

        client, token, _account_id, cache_path = calendar_api
        dek = "a" * 64
        conn = open_cache(cache_path, dek)
        cal1_id = upsert_calendar(
            conn,
            uid="fb-cal-1",
            href="/caldav/fb-cal-1/",
            displayname="Calendar 1",
            color="#4285f4",
        )
        cal2_id = upsert_calendar(
            conn,
            uid="fb-cal-2",
            href="/caldav/fb-cal-2/",
            displayname="Calendar 2",
            color="#ea4335",
        )
        upsert_event(
            conn,
            uid="fb-evt-1",
            href="/caldav/fb-cal-1/fb-evt-1.ics",
            etag="e1",
            calendar_id=cal1_id,
            ical_text=(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                "BEGIN:VEVENT\r\n"
                "UID:fb-evt-1\r\n"
                "DTSTART:20260701T100000Z\r\n"
                "DTEND:20260701T110000Z\r\n"
                "SUMMARY:Cal1 Event\r\n"
                "END:VEVENT\r\n"
                "END:VCALENDAR\r\n"
            ),
        )
        upsert_event(
            conn,
            uid="fb-evt-2",
            href="/caldav/fb-cal-2/fb-evt-2.ics",
            etag="e2",
            calendar_id=cal2_id,
            ical_text=(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                "BEGIN:VEVENT\r\n"
                "UID:fb-evt-2\r\n"
                "DTSTART:20260701T140000Z\r\n"
                "DTEND:20260701T150000Z\r\n"
                "SUMMARY:Cal2 Event\r\n"
                "END:VEVENT\r\n"
                "END:VCALENDAR\r\n"
            ),
        )
        conn.close()

        resp_all = client.post(
            "/api/v1/calendar/free-busy",
            json={"start": "2026-07-01T00:00:00Z", "end": "2026-07-02T00:00:00Z"},
            headers=auth_header(token),
        )
        assert resp_all.status_code == 200
        all_data = json.loads(resp_all.data)["data"]
        assert len(all_data) == 2

        resp_filtered = client.post(
            "/api/v1/calendar/free-busy",
            json={
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-02T00:00:00Z",
                "calendar_ids": [cal1_id],
            },
            headers=auth_header(token),
        )
        assert resp_filtered.status_code == 200
        filtered_data = json.loads(resp_filtered.data)["data"]
        assert len(filtered_data) == 1
        assert filtered_data[0]["summary"] == "Cal1 Event"
        assert filtered_data[0]["calendar_id"] == cal1_id


class TestCreateCalendarSchema:
    def test_create_returns_full_object(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, _cache_path = calendar_api

        mock_session = MagicMock()
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
            patch(
                "app.modules.calendar.services.caldav.create_calendar",
                return_value="/caldav/new-cal/",
            ),
        ):
            resp = client.post(
                "/api/v1/calendar/calendars",
                json={"name": "Test Calendar", "color": "#ff0000"},
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        for key in ("id", "uid", "name", "color", "is_default"):
            assert key in data, f"Missing field: {key}"
        assert data["name"] == "Test Calendar"
        assert data["color"] == "#ff0000"
        assert isinstance(data["is_default"], bool)


class TestUpdateCalendarSchema:
    def test_update_returns_full_object(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api
        cal_id = _seed_calendar_cache(cache_path)

        mock_session = MagicMock()
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
            patch("app.modules.calendar.services.caldav.update_calendar_props"),
        ):
            resp = client.put(
                f"/api/v1/calendar/calendars/{cal_id}",
                json={"name": "Updated Calendar", "color": "#00ff00"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        for key in ("id", "uid", "name", "color", "is_default"):
            assert key in data, f"Missing field: {key}"
        assert data["name"] == "Updated Calendar"
        assert data["color"] == "#00ff00"
        assert data["id"] == cal_id


class TestCreateEventSchema:
    def test_create_returns_full_object(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api

        from app.modules.calendar.services.cache_db import open_cache, upsert_calendar

        conn = open_cache(cache_path, "a" * 64)
        cal_id = upsert_calendar(
            conn, uid="evt-cal", href="/caldav/evt-cal/", displayname="Event Cal"
        )
        conn.close()

        mock_session = MagicMock()
        mock_session.put.return_value = MagicMock(status_code=204, headers={})
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
            patch(
                "app.modules.calendar.services.caldav.create_event",
                return_value=("/caldav/evt-cal/evt.ics", "etag-evt"),
            ),
        ):
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    "summary": "Schema Event",
                    "start": "2026-08-01T10:00:00Z",
                    "end": "2026-08-01T11:00:00Z",
                    "location": "Room A",
                    "description": "Test description",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        for key in (
            "id",
            "uid",
            "summary",
            "description",
            "location",
            "start",
            "end",
            "is_all_day",
            "calendar_id",
        ):
            assert key in data, f"Missing field: {key}"
        assert data["summary"] == "Schema Event"
        assert data["calendar_id"] == cal_id
        assert data["is_all_day"] is False


class TestAllDayEvents:
    def test_create_all_day_event_uses_value_date(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api

        from app.modules.calendar.services.cache_db import open_cache, upsert_calendar

        conn = open_cache(cache_path, "a" * 64)
        cal_id = upsert_calendar(
            conn, uid="ad-cal", href="/caldav/ad-cal/", displayname="All Day Cal"
        )
        conn.close()

        mock_session = MagicMock()
        mock_session.put.return_value = MagicMock(status_code=204, headers={})
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
            patch(
                "app.modules.calendar.services.caldav.create_event",
                return_value=("/caldav/ad-cal/ad.ics", "etag-ad"),
            ) as mock_create,
        ):
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    "summary": "All Day Event",
                    "start": "2026-09-01",
                    "end": "2026-09-02",
                    "is_all_day": True,
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201, resp.data
        data = json.loads(resp.data)["data"]
        assert data["is_all_day"] is True
        ical_text = mock_create.call_args[0][2]
        # The all-day flag must reach the iCalendar body (regression: the
        # controller used to pass "is_all_day", which generate_icalendar
        # ignored — silently producing a timed event).
        assert "DTSTART;VALUE=DATE:20260901" in ical_text
        assert "DTEND;VALUE=DATE:20260902" in ical_text

    def test_update_preserves_all_day_flag(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api

        from app.modules.calendar.services.cache_db import open_cache, upsert_calendar, upsert_event

        conn = open_cache(cache_path, "a" * 64)
        cal_id = upsert_calendar(
            conn, uid="ad-cal-2", href="/caldav/ad-cal-2/", displayname="All Day Cal 2"
        )
        upsert_event(
            conn,
            uid="all-day-evt",
            href="/caldav/ad-cal-2/all-day-evt.ics",
            etag="etag-ad-1",
            calendar_id=cal_id,
            ical_text=(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                "BEGIN:VEVENT\r\n"
                "UID:all-day-evt\r\n"
                "DTSTART;VALUE=DATE:20260901\r\n"
                "DTEND;VALUE=DATE:20260902\r\n"
                "SUMMARY:Holiday\r\n"
                "END:VEVENT\r\n"
                "END:VCALENDAR\r\n"
            ),
        )
        conn.close()

        list_resp = client.get("/api/v1/calendar/search?q=Holiday", headers=auth_header(token))
        event_id = json.loads(list_resp.data)["data"][0]["id"]
        detail = json.loads(
            client.get(f"/api/v1/calendar/events/{event_id}", headers=auth_header(token)).data
        )["data"]
        assert detail["is_all_day"] is True

        mock_session = MagicMock()
        mock_session.put.return_value = MagicMock(status_code=204, headers={"ETag": "etag-ad-2"})
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
        ):
            # Update an unrelated field; the all-day flag must survive the merge
            resp = client.put(
                f"/api/v1/calendar/events/{event_id}",
                json={"location": "Beach"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200, resp.data
        data = json.loads(resp.data)["data"]
        assert data["is_all_day"] is True
        body = mock_session.put.call_args[1]["data"].decode("utf-8")
        assert "DTSTART;VALUE=DATE:20260901" in body
        assert "DTEND;VALUE=DATE:20260902" in body


class TestUpdateEventSchema:
    def test_update_returns_full_object(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api
        _seed_calendar_cache(cache_path)
        list_resp = client.get("/api/v1/calendar/search?q=Team+Meeting", headers=auth_header(token))
        event_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_session = MagicMock()
        mock_session.put.return_value = MagicMock(status_code=204, headers={})
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
        ):
            resp = client.put(
                f"/api/v1/calendar/events/{event_id}",
                json={"summary": "Schema Updated", "location": "Room B"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        for key in (
            "id",
            "uid",
            "summary",
            "description",
            "location",
            "start",
            "end",
            "is_all_day",
            "calendar_id",
        ):
            assert key in data, f"Missing field: {key}"
        assert data["summary"] == "Schema Updated"
        assert data["location"] == "Room B"
        assert data["id"] == event_id


class TestEventLifecycle:
    def test_create_list_get_delete(self, app, calendar_api):
        from unittest.mock import MagicMock, patch

        client, token, _account_id, cache_path = calendar_api

        from app.modules.calendar.services.cache_db import open_cache, upsert_calendar

        conn = open_cache(cache_path, "a" * 64)
        cal_id = upsert_calendar(conn, uid="lc-cal", href="/caldav/lc/", displayname="Lifecycle")
        conn.close()

        mock_session = MagicMock()
        mock_session.put.return_value = MagicMock(status_code=204, headers={})
        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
            patch(
                "app.modules.calendar.services.caldav.create_event",
                return_value=("/caldav/lc/evt.ics", "etag-lc"),
            ),
        ):
            create_resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    "summary": "Lifecycle Event",
                    "start": "2026-05-27T10:00:00Z",
                    "end": "2026-05-27T11:00:00Z",
                },
                headers=auth_header(token),
            )
        assert create_resp.status_code == 201
        create_data = json.loads(create_resp.data)["data"]
        uid = create_data["uid"]
        assert create_data["summary"] == "Lifecycle Event"

        list_resp = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events",
            headers=auth_header(token),
        )
        assert list_resp.status_code == 200
        list_data = json.loads(list_resp.data)["data"]
        assert len(list_data) == 1
        assert list_data[0]["summary"] == "Lifecycle Event"
        event_id = list_data[0]["id"]

        get_resp = client.get(
            f"/api/v1/calendar/events/{event_id}",
            headers=auth_header(token),
        )
        assert get_resp.status_code == 200
        get_data = json.loads(get_resp.data)["data"]
        assert get_data["summary"] == "Lifecycle Event"
        assert get_data["uid"] == uid

        with (
            patch(
                "app.api.controllers.calendar._get_caldav_session",
                return_value=(mock_session, [], "http://localhost:5232", "pass"),
            ),
            patch("app.modules.calendar.services.caldav.delete_event", return_value=True),
        ):
            delete_resp = client.delete(
                f"/api/v1/calendar/events/{event_id}",
                headers=auth_header(token),
            )
        assert delete_resp.status_code == 204

        list_resp2 = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events",
            headers=auth_header(token),
        )
        assert list_resp2.status_code == 200
        assert json.loads(list_resp2.data)["data"] == []
