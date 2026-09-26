import contextlib
import os
from unittest.mock import MagicMock, patch

from app.shared.db import db
from app.shared.models.core import Domain


def _safe_unlink(path):
    with contextlib.suppress(OSError):
        os.unlink(path)


def _fetch_scalar(conn, sql):
    row = conn.execute(sql).fetchone()
    if row is None:
        raise AssertionError("expected row missing: " + sql)
    return row[0]


def _setup_test_env(app, account_id, with_caldav=True, with_cache=True):
    paths = {}
    with app.app_context():
        from app.modules.calendar.services.cache import get_cache_path
        from app.shared.db import db
        from app.shared.models.core import CustomerAccount

        account = db.session.get(CustomerAccount, account_id)
        if account is None:
            raise AssertionError("fixture account missing")
        domain = db.session.get(Domain, account.domain_id)
        if domain is None:
            raise AssertionError("fixture domain missing")
        if with_caldav:
            domain.caldav_host = "localhost"
            domain.caldav_port = 5232
            domain.caldav_use_tls = False
        if with_cache:
            paths["cache"] = get_cache_path(account)
            if os.path.exists(paths["cache"]):
                _safe_unlink(paths["cache"])
        db.session.commit()
    return paths


def test_calendar_index_no_caldav_config(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id, with_caldav=False)
    try:
        resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"not configured" in resp.data
    finally:
        if paths.get("cache"):
            _safe_unlink(paths["cache"])


def test_calendar_index_empty(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events"):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"calendar-grid" in resp.data
        assert b"Sync calendars" in resp.data
        assert b"New event" not in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_calendar_index_with_calendars(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "My Calendar", "#4285f4"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events"):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"My Calendar" in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_event_new_redirects_to_index(authed_client, app):
    """U12.56b: the full-page form is gone; /events/new opens the dialog."""
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.commit()
        conn.close()

    try:
        resp = client.get("/app/calendar/events/new")
        assert resp.status_code == 302
        assert "/app/calendar/" in resp.headers["Location"]
        assert "new=1" in resp.headers["Location"]
    finally:
        _safe_unlink(paths["cache"])


def test_event_new_prefill_params_preserved(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get(
            "/app/calendar/events/new?summary=Lunch&attendee=alice%40example.com&dtstart=2026-03-02T12%3A00"
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "summary=Lunch" in location
        assert "attendee=alice" in location
        assert "new=1" in location
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_create_validation_error(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.commit()
        conn.close()

    try:
        resp = client.post(
            "/app/calendar/api/events",
            json={"summary": "", "dtstart_date": "", "calendar_id": 1},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_create_happy_path(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.commit()
        conn.close()

    try:
        with (
            patch("app.modules.calendar.controllers.events_api.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.events_api._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.create_event.return_value = ("/test/new.ics", "etag-new")
            resp = client.post(
                "/app/calendar/api/events",
                json={
                    "summary": "API Event",
                    "dtstart_date": "2026-03-02",
                    "dtstart_time": "10:00",
                    "dtend_date": "2026-03-02",
                    "dtend_time": "11:00",
                    "calendar_id": 1,
                    "timezone": "UTC",
                },
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["event_id"]

        from app.modules.calendar.services.cache_db import open_cache as _open

        with app.app_context():
            key = get_user_key(user_id)
            conn = _open(paths["cache"], key)
            row = conn.execute(
                "SELECT summary, dtstart FROM calendar_events WHERE id = ?",
                (data["event_id"],),
            ).fetchone()
            assert row is not None
            assert row[0] == "API Event"
            assert row[1].startswith("2026-03-02T10:00")
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_event_detail(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, dtend, all_day, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-1",
                "/test/evt1.ics",
                "etag1",
                1,
                "Team Meeting",
                "2025-01-15T10:00:00+00:00",
                "2025-01-15T11:00:00+00:00",
                0,
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Team Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        event_id = _fetch_scalar(conn, "SELECT id FROM calendar_events WHERE uid = 'evt-1'")
        conn.close()

    try:
        resp = client.get(f"/app/calendar/events/{event_id}")
        assert resp.status_code == 200
        assert b"Team Meeting" in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_event_detail_not_found(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get("/app/calendar/events/99999")
        assert resp.status_code == 302
    finally:
        _safe_unlink(paths["cache"])


def test_event_detail_shows_user_timezone(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key
    from app.shared.models.core import CustomerSettings

    with app.app_context():
        settings = CustomerSettings()
        settings.customer_id = user_id
        settings.timezone = "Australia/Adelaide"
        db.session.add(settings)
        db.session.commit()

        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-tz", "/test/caltz/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, dtend, all_day, timezone, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-tz",
                "/test/evttz.ics",
                "etag-tz",
                1,
                "TZ Event",
                "2026-05-14T12:00:00",
                "2026-05-14T13:00:00",
                0,
                "Australia/Adelaide",
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:TZ Event\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        event_id = _fetch_scalar(conn, "SELECT id FROM calendar_events WHERE uid = 'evt-tz'")
        conn.close()

    try:
        resp = client.get(f"/app/calendar/events/{event_id}")
        assert resp.status_code == 200
        assert b"Australia/Adelaide" in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_update_happy_path(authed_client, app):
    """U12.56b: editing goes through the JSON update endpoint."""
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, dtend, all_day, timezone, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-2",
                "/test/evt2.ics",
                "etag2",
                1,
                "Standup",
                "2026-01-15T09:00:00",
                "2026-01-15T09:30:00",
                0,
                "UTC",
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Standup\r\nDTSTART:20260115T090000Z\r\nDTEND:20260115T093000Z\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        event_id = _fetch_scalar(conn, "SELECT id FROM calendar_events WHERE uid = 'evt-2'")
        conn.close()

    try:
        with (
            patch("app.modules.calendar.controllers.events_api.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.events_api._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.update_event.return_value = "etag-new"
            resp = client.put(
                f"/app/calendar/api/events/{event_id}",
                json={
                    "summary": "Standup v2",
                    "dtstart_date": "2026-01-15",
                    "dtstart_time": "10:00",
                    "dtend_date": "2026-01-15",
                    "dtend_time": "10:30",
                    "calendar_id": 1,
                    "timezone": "UTC",
                },
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

        from app.modules.calendar.services.cache_db import open_cache as _open

        with app.app_context():
            key = get_user_key(user_id)
            conn = _open(paths["cache"], key)
            row = conn.execute(
                "SELECT summary, dtstart FROM calendar_events WHERE id = ?", (event_id,)
            ).fetchone()
            assert row is not None
            assert row[0] == "Standup v2"
            assert row[1].startswith("2026-01-15T10:00")
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_update_not_found(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.put(
            "/app/calendar/api/events/99999",
            json={"summary": "X", "dtstart_date": "2026-01-15", "calendar_id": 1},
        )
        assert resp.status_code == 404
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_move_updates_times(authed_client, app):
    """U12.56e/U12.20: drag-move persists new dtstart/dtend only."""
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, dtend, all_day, timezone, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-mv",
                "/test/evtmv.ics",
                "etag-mv",
                1,
                "Movable",
                "2026-01-15T09:00:00",
                "2026-01-15T10:00:00",
                0,
                "UTC",
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Movable\r\nDTSTART:20260115T090000Z\r\nDTEND:20260115T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        event_id = _fetch_scalar(conn, "SELECT id FROM calendar_events WHERE uid = 'evt-mv'")
        conn.close()

    try:
        with (
            patch("app.modules.calendar.controllers.events_api.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.events_api._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.update_event.return_value = "etag-mv2"
            resp = client.post(
                f"/app/calendar/api/events/{event_id}/move",
                json={
                    "dtstart": "2026-01-16T11:00:00",
                    "dtend": "2026-01-16T12:00:00",
                },
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

        from app.modules.calendar.services.cache_db import open_cache as _open

        with app.app_context():
            key = get_user_key(user_id)
            conn = _open(paths["cache"], key)
            row = conn.execute(
                "SELECT summary, dtstart, dtend FROM calendar_events WHERE id = ?", (event_id,)
            ).fetchone()
            assert row is not None
            assert row[0] == "Movable"
            assert row[1].startswith("2026-01-16T11:00")
            assert row[2].startswith("2026-01-16T12:00")
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_get_single(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-get",
                "/test/evtget.ics",
                "etag-get",
                1,
                "Single Event",
                "2026-01-15T09:00:00+00:00",
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Single Event\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        event_id = _fetch_scalar(conn, "SELECT id FROM calendar_events WHERE uid = 'evt-get'")
        conn.close()

    try:
        resp = client.get(f"/app/calendar/api/events/{event_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["summary"] == "Single Event"
        assert data["calendar_color"] == "#4285f4"
        assert data["calendar_name"] == "Work"
    finally:
        _safe_unlink(paths["cache"])


def test_event_delete(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-3",
                "/test/evt3.ics",
                "etag3",
                1,
                "Delete Me",
                "2025-01-15T09:00:00+00:00",
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Delete Me\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        event_id = _fetch_scalar(conn, "SELECT id FROM calendar_events WHERE uid = 'evt-3'")
        conn.close()

    try:
        with patch("app.modules.calendar.controllers.events.caldav") as mock_caldav:
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.delete_event.return_value = True
            resp = client.post(f"/app/calendar/events/{event_id}/delete")
        assert resp.status_code == 302
    finally:
        _safe_unlink(paths["cache"])


def test_api_events(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, dtend, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-api",
                "/test/api.ics",
                "e1",
                1,
                "API Event",
                "2025-01-15T10:00:00+00:00",
                "2025-01-15T11:00:00+00:00",
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:API Event\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        conn.close()

    import json

    try:
        resp = client.get(
            "/app/calendar/api/events?start=2025-01-01T00:00:00&end=2025-12-31T23:59:59"
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1
        assert data[0]["summary"] == "API Event"
    finally:
        _safe_unlink(paths["cache"])


def test_api_search(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color) VALUES (?, ?, ?, ?)",
            ("cal-1", "/test/cal1/", "Work", "#4285f4"),
        )
        conn.execute(
            "INSERT INTO calendar_events (uid, href, etag, calendar_id, summary, dtstart, raw_ical) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-search",
                "/test/search.ics",
                "e1",
                1,
                "Search Test Event",
                "2025-01-15T10:00:00+00:00",
                "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Search Test Event\r\nEND:VEVENT\r\nEND:VCALENDAR",
            ),
        )
        conn.commit()
        conn.close()

    import json

    try:
        resp = client.get("/app/calendar/api/search?q=Search")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1
        assert data[0]["summary"] == "Search Test Event"
    finally:
        _safe_unlink(paths["cache"])


def test_api_search_too_short(authed_client, app):
    client, _user_id, _account_id = authed_client
    resp = client.get("/app/calendar/api/search?q=a")
    assert resp.status_code == 200
    import json

    assert json.loads(resp.data) == []


def test_calendar_sync(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch(
            "app.modules.calendar.controllers.views._sync_calendars_and_events"
        ) as mock_sync:
            resp = client.post("/app/calendar/sync")
        assert resp.status_code == 302
        mock_sync.assert_called_once()
    finally:
        _safe_unlink(paths["cache"])


def test_calendar_toggle_visibility(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color, is_visible) VALUES (?, ?, ?, ?, ?)",
            ("cal-tog", "/test/tog/", "Toggle Test", "#4285f4", 1),
        )
        conn.commit()
        cal_id = _fetch_scalar(conn, "SELECT id FROM calendars WHERE uid = 'cal-tog'")
        conn.close()

    try:
        resp = client.post(f"/app/calendar/calendars/{cal_id}/toggle")
        assert resp.status_code == 302

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            row = conn.execute(
                "SELECT is_visible FROM calendars WHERE id = ?", (cal_id,)
            ).fetchone()
            if row is None:
                raise AssertionError("expected calendar row")
            assert row[0] == 0
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_create_unknown_calendar(authed_client, app):
    """U12.56b: creating against a missing calendar is a structured 404."""
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post(
            "/app/calendar/api/events",
            json={"summary": "X", "dtstart_date": "2026-01-15", "calendar_id": 999},
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["ok"] is False
    finally:
        _safe_unlink(paths["cache"])


def test_auto_create_default_calendar(authed_client, app):
    _client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    try:
        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)

            from unittest.mock import MagicMock, patch

            from app.modules.calendar.controllers.views import (
                _get_caldav_config,
                _sync_calendars_and_events,
            )
            from app.shared.models.core import CustomerAccount

            account_obj = db.session.get(CustomerAccount, account_id)
            config = _get_caldav_config(account_obj)

            with (
                patch("app.modules.calendar.services.sync.caldav") as mock_caldav,
                patch(
                    "app.modules.calendar.controllers.views._get_credentials",
                    return_value="test-password",
                ),
            ):
                mock_session = MagicMock()
                mock_caldav.discover_calendars.return_value = (mock_session, [])
                mock_caldav.create_calendar.return_value = "http://localhost:5232/test/calendar/"

                _sync_calendars_and_events(conn, account_obj, config)

                mock_caldav.create_calendar.assert_called_once()
                call_kwargs = mock_caldav.create_calendar.call_args
                assert call_kwargs[1]["name"] == "Test"

            cals = conn.execute("SELECT * FROM calendars").fetchall()
            assert len(cals) == 1
            assert cals[0][3] == "Test"
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_delete_default_calendar_blocked(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color, is_default) VALUES (?, ?, ?, ?, ?)",
            ("cal-def", "/cals/def/", "Default", "#4285f4", 1),
        )
        conn.commit()
        cal_id = _fetch_scalar(conn, "SELECT id FROM calendars WHERE uid = 'cal-def'")
        conn.close()

    try:
        resp = client.post(f"/app/calendar/calendars/{cal_id}/delete")
        assert resp.status_code == 302

        from app.shared.keys import get_user_key

        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        row = conn.execute("SELECT id FROM calendars WHERE id = ?", (cal_id,)).fetchone()
        assert row is not None
        conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_index_shows_no_calendars_message(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events"):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"calendar-grid" in resp.data
        assert b"Sync calendars" in resp.data
        assert b"New event" not in resp.data
    finally:
        _safe_unlink(paths["cache"])


def _setup_calendar_and_cache(app, account_id):
    paths = _setup_test_env(app, account_id)
    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(app.config.get("_test_user_id", account_id))
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO calendars (uid, href, displayname, color, is_default) VALUES (?, ?, ?, ?, ?)",
            ("cal-qc", "/test/qc/", "Test Cal", "#4285f4", 1),
        )
        conn.commit()
        cal_id = _fetch_scalar(conn, "SELECT id FROM calendars WHERE uid = 'cal-qc'")
        conn.close()
    return paths, cal_id


def test_quick_create_no_account(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with client.session_transaction() as sess:
            sess["active_account_id"] = None
        resp = client.post(
            "/app/calendar/api/events/quick-create",
            json={"summary": "Test", "dtstart": "2026-05-11T09:00:00", "calendar_id": 1},
        )
        assert resp.status_code == 400
        import json

        data = json.loads(resp.data)
        assert not data["ok"]
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_missing_dtstart(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post(
            "/app/calendar/api/events/quick-create",
            json={"summary": "Test", "calendar_id": 1},
        )
        assert resp.status_code == 400
        import json

        data = json.loads(resp.data)
        assert not data["ok"]
        assert "Start time" in data["error"]
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_missing_calendar(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post(
            "/app/calendar/api/events/quick-create",
            json={"summary": "Test", "dtstart": "2026-05-11T09:00:00"},
        )
        assert resp.status_code == 400
        import json

        data = json.loads(resp.data)
        assert not data["ok"]
        assert "Calendar" in data["error"]
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_calendar_not_found(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post(
            "/app/calendar/api/events/quick-create",
            json={"summary": "Test", "dtstart": "2026-05-11T09:00:00", "calendar_id": 9999},
        )
        assert resp.status_code == 404
        import json

        data = json.loads(resp.data)
        assert not data["ok"]
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_timed_event(authed_client, app):
    client, user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with (
            patch("app.modules.calendar.controllers.views.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.views._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.create_event.return_value = ("/test/qc/evt.ics", "etag-1")

            resp = client.post(
                "/app/calendar/api/events/quick-create",
                json={
                    "summary": "Quick Meeting",
                    "dtstart": "2026-05-11T09:00:00",
                    "dtend": "2026-05-11T10:00:00",
                    "calendar_id": cal_id,
                    "all_day": False,
                },
            )

        assert resp.status_code == 200
        import json

        data = json.loads(resp.data)
        assert data["ok"]
        assert data["event_id"] is not None

        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            events = conn.execute(
                "SELECT summary, dtstart, dtend, all_day FROM calendar_events"
            ).fetchall()
            conn.close()
        assert len(events) == 1
        assert events[0][0] == "Quick Meeting"
        assert events[0][3] == 0
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_all_day_event(authed_client, app):
    client, user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with (
            patch("app.modules.calendar.controllers.views.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.views._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.create_event.return_value = ("/test/qc/allday.ics", "etag-2")

            resp = client.post(
                "/app/calendar/api/events/quick-create",
                json={
                    "summary": "Day Off",
                    "dtstart": "2026-05-11",
                    "calendar_id": cal_id,
                    "all_day": True,
                },
            )

        assert resp.status_code == 200
        import json

        data = json.loads(resp.data)
        assert data["ok"]

        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            events = conn.execute("SELECT summary, all_day FROM calendar_events").fetchall()
            conn.close()
        assert len(events) == 1
        assert events[0][0] == "Day Off"
        assert events[0][1] == 1
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_default_summary(authed_client, app):
    client, user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with (
            patch("app.modules.calendar.controllers.views.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.views._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.create_event.return_value = ("/test/qc/def.ics", "etag-3")

            resp = client.post(
                "/app/calendar/api/events/quick-create",
                json={
                    "summary": "",
                    "dtstart": "2026-05-11T14:00:00",
                    "dtend": "2026-05-11T15:00:00",
                    "calendar_id": cal_id,
                    "all_day": False,
                },
            )

        assert resp.status_code == 200
        import json

        data = json.loads(resp.data)
        assert data["ok"]

        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            summary = _fetch_scalar(conn, "SELECT summary FROM calendar_events")
            conn.close()
        assert summary == "(no title)"
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_no_caldav_config(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id, with_caldav=False)
    try:
        resp = client.post(
            "/app/calendar/api/events/quick-create",
            json={"summary": "Test", "dtstart": "2026-05-11T09:00:00", "calendar_id": 1},
        )
        assert resp.status_code == 400
        import json

        data = json.loads(resp.data)
        assert not data["ok"]
        assert "CalDAV" in data["error"]
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_caldav_failure(authed_client, app):
    client, _user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with (
            patch("app.modules.calendar.controllers.views.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.views._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_caldav.discover_calendars.side_effect = Exception("connection refused")

            resp = client.post(
                "/app/calendar/api/events/quick-create",
                json={
                    "summary": "Fail Event",
                    "dtstart": "2026-05-11T09:00:00",
                    "dtend": "2026-05-11T10:00:00",
                    "calendar_id": cal_id,
                    "all_day": False,
                },
            )

        assert resp.status_code == 500
        import json

        data = json.loads(resp.data)
        assert not data["ok"]
        assert "Failed" in data["error"]
    finally:
        _safe_unlink(paths["cache"])


def test_quick_create_includes_popover_html(authed_client, app):
    """U12.56: the shell ships the popover + editor + FAB; grid cells are
    rendered by the versioned static JS (time-grid/month classes live there)."""
    client, _user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events"):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"quick-create-popover" in resp.data
        assert b"qc-summary" in resp.data
        assert b"qc-calendar" in resp.data
        assert b"qc-save" in resp.data
        assert b"qc-more-options" in resp.data
        assert b"cal-editor" in resp.data
        assert b"ce-title" in resp.data
        assert b"cal-fab" in resp.data
        assert b"js/calendar/main.js" in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_sync_error_shows_warning_banner(authed_client, app):
    client, _user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch(
            "app.modules.calendar.controllers.views._sync_calendars_and_events",
            side_effect=Exception("connection refused"),
        ):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"Unable to connect to the calendar server" in resp.data
        assert b"caldav-warning" in resp.data
        assert b"Retry" in resp.data
        assert b"calendar-grid" in resp.data
    finally:
        _safe_unlink(paths["cache"])


def test_sync_success_no_warning_banner(authed_client, app):
    client, _user_id, account_id = authed_client
    paths, _ = _setup_calendar_and_cache(app, account_id)
    try:
        resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"caldav-warning" not in resp.data
    finally:
        if os.path.exists(paths["cache"]):
            _safe_unlink(paths["cache"])


# ---- multiple notifications (U12.30/U12.31) ----


def _create_event_with_reminders(client, app, paths, user_id, reminders, summary="Multi Rem"):
    """POST to the JSON event API with a reminders array; return (resp, event_id)."""
    with (
        patch("app.modules.calendar.controllers.events_api.caldav") as mock_caldav,
        patch(
            "app.modules.calendar.controllers.events_api._get_credentials",
            return_value="test-password",
        ),
    ):
        mock_session = MagicMock()
        mock_caldav.discover_calendars.return_value = (mock_session, [])
        mock_caldav.create_event.return_value = ("/test/multi.ics", "etag-multi")
        resp = client.post(
            "/app/calendar/api/events",
            json={
                "summary": summary,
                "dtstart_date": "2026-06-01",
                "dtstart_time": "10:00",
                "dtend_date": "2026-06-01",
                "dtend_time": "11:00",
                "calendar_id": 1,
                "timezone": "UTC",
                "reminders": reminders,
            },
        )
    return resp


def _fetch_reminders(conn, event_id):
    # Column order per calendar_reminders schema: id, event_id, trigger_val, action, description
    rows = conn.execute(
        "SELECT trigger_val, action FROM calendar_reminders WHERE event_id = ? ORDER BY id",
        (event_id,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def test_api_event_create_multiple_reminders(authed_client, app):
    client, user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        resp = _create_event_with_reminders(
            client,
            app,
            paths,
            user_id,
            [
                {"action": "DISPLAY", "trigger": "-PT15M"},
                {"action": "EMAIL", "trigger": "-P1D"},
                {"action": "DISPLAY", "trigger": "-PT90M"},
            ],
        )
        assert resp.status_code == 200
        import json as _json

        data = _json.loads(resp.data)
        assert data["ok"] is True

        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            reminders = _fetch_reminders(conn, data["event_id"])
            row = conn.execute(
                "SELECT raw_ical FROM calendar_events WHERE id = ?", (data["event_id"],)
            ).fetchone()
            assert row is not None
            raw_ical = row[0]
            conn.close()

        assert reminders == [
            ("-PT15M", "DISPLAY"),
            ("-P1D", "EMAIL"),
            ("-PT90M", "DISPLAY"),
        ]
        assert raw_ical.count("BEGIN:VALARM") == 3
        assert "ACTION:EMAIL" in raw_ical
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_create_reminders_accept_type_alias(authed_client, app):
    """The REST API shape ({type, trigger}) is tolerated by the dialog API parser."""
    client, user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        resp = _create_event_with_reminders(
            client,
            app,
            paths,
            user_id,
            [{"type": "EMAIL", "trigger": "-PT30M"}],
        )
        assert resp.status_code == 200
        import json as _json

        event_id = _json.loads(resp.data)["event_id"]

        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            assert _fetch_reminders(conn, event_id) == [("-PT30M", "EMAIL")]
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_create_reminder_validation_errors(authed_client, app):
    client, user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    bad_payloads = [
        # unknown action
        [{"action": "SMS", "trigger": "-PT15M"}],
        # not a negative ISO duration
        [{"action": "DISPLAY", "trigger": "PT15M"}],
        [{"action": "DISPLAY", "trigger": "-P"}],
        [{"action": "DISPLAY", "trigger": ""}],
        # exact duplicate (same action + trigger)
        [
            {"action": "DISPLAY", "trigger": "-PT15M"},
            {"action": "DISPLAY", "trigger": "-PT15M"},
        ],
        # over the cap
        [{"action": "DISPLAY", "trigger": f"-PT{n}M"} for n in range(1, 12)],
        # not a list of dicts
        ["DISPLAY"],
    ]
    try:
        for reminders in bad_payloads:
            resp = _create_event_with_reminders(client, app, paths, user_id, reminders)
            assert resp.status_code == 400, reminders
            import json as _json

            data = _json.loads(resp.data)
            assert data["ok"] is False
            assert data["error"]
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_create_reminders_optional(authed_client, app):
    """No reminders key at all -> zero alarms, event saves fine."""
    client, user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        resp = _create_event_with_reminders(client, app, paths, user_id, None)
        assert resp.status_code == 200
        import json as _json

        event_id = _json.loads(resp.data)["event_id"]

        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            assert _fetch_reminders(conn, event_id) == []
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_update_replaces_reminders(authed_client, app):
    client, user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        resp = _create_event_with_reminders(
            client,
            app,
            paths,
            user_id,
            [{"action": "DISPLAY", "trigger": "-PT15M"}],
            summary="Replace Me",
        )
        import json as _json

        event_id = _json.loads(resp.data)["event_id"]

        with (
            patch("app.modules.calendar.controllers.events_api.caldav") as mock_caldav,
            patch(
                "app.modules.calendar.controllers.events_api._get_credentials",
                return_value="test-password",
            ),
        ):
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.update_event.return_value = "etag-updated"
            resp = client.put(
                f"/app/calendar/api/events/{event_id}",
                json={
                    "summary": "Replace Me",
                    "dtstart_date": "2026-06-01",
                    "dtstart_time": "10:00",
                    "dtend_date": "2026-06-01",
                    "dtend_time": "11:00",
                    "calendar_id": 1,
                    "timezone": "UTC",
                    "reminders": [
                        {"action": "EMAIL", "trigger": "-PT5M"},
                        {"action": "DISPLAY", "trigger": "-P1W"},
                    ],
                },
            )
        assert resp.status_code == 200
        assert _json.loads(resp.data)["ok"] is True

        from app.modules.calendar.services.cache_db import open_cache
        from app.shared.keys import get_user_key

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            assert _fetch_reminders(conn, event_id) == [
                ("-PT5M", "EMAIL"),
                ("-P1W", "DISPLAY"),
            ]
            conn.close()
    finally:
        _safe_unlink(paths["cache"])


def test_api_event_get_returns_reminders(authed_client, app):
    """Regression: the editor fetches reminders via GET; they must be serialized."""
    client, user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        resp = _create_event_with_reminders(
            client,
            app,
            paths,
            user_id,
            [
                {"action": "DISPLAY", "trigger": "-PT15M"},
                {"action": "EMAIL", "trigger": "-P1D"},
            ],
            summary="Fetch Me",
        )
        import json as _json

        event_id = _json.loads(resp.data)["event_id"]
        resp = client.get(f"/app/calendar/api/events/{event_id}")
        assert resp.status_code == 200
        data = _json.loads(resp.data)
        assert data["reminders"] == [
            {"trigger_val": "-PT15M", "action": "DISPLAY"},
            {"trigger_val": "-P1D", "action": "EMAIL"},
        ]
    finally:
        _safe_unlink(paths["cache"])


def test_event_detail_humanizes_reminders(authed_client, app):
    client, user_id, account_id = authed_client
    paths, _cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        resp = _create_event_with_reminders(
            client,
            app,
            paths,
            user_id,
            [
                {"action": "DISPLAY", "trigger": "-PT15M"},
                {"action": "EMAIL", "trigger": "-P1DT2H"},
                {"action": "DISPLAY", "trigger": "-PT0M"},
            ],
            summary="Humanize Me",
        )
        import json as _json

        event_id = _json.loads(resp.data)["event_id"]
        resp = client.get(f"/app/calendar/events/{event_id}")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "15 minutes before" in body
        assert "1 day, 2 hours before" in body
        assert "At time of event" in body
        assert "Notification" in body
        assert "Email" in body
        assert "-PT15M" not in body
    finally:
        _safe_unlink(paths["cache"])


def test_humanize_reminder_unit(app):
    from app.modules.calendar.controllers.helpers import _humanize_reminder

    with app.test_request_context():
        assert _humanize_reminder("-PT15M", "DISPLAY") == {
            "when": "15 minutes before",
            "kind": "Notification",
        }
        assert _humanize_reminder("-P1W", "EMAIL") == {"when": "1 week before", "kind": "Email"}
        assert _humanize_reminder("-PT0M", None) == {
            "when": "At time of event",
            "kind": "Notification",
        }
        assert _humanize_reminder("garbage", "DISPLAY")["when"] == "garbage"


def test_parse_negative_duration_unit():
    from app.modules.calendar.controllers.helpers import parse_negative_duration

    assert parse_negative_duration("-PT15M") == (0, 0, 0, 15)
    assert parse_negative_duration("-P1DT2H") == (0, 1, 2, 0)
    assert parse_negative_duration("-PT0M") == (0, 0, 0, 0)
    assert parse_negative_duration("-P2W") == (2, 0, 0, 0)
    assert parse_negative_duration("-P") is None
    assert parse_negative_duration("-PT") is None
    assert parse_negative_duration("PT15M") is None
    assert parse_negative_duration("") is None
    assert parse_negative_duration(None) is None
    assert parse_negative_duration("-PT15") is None
