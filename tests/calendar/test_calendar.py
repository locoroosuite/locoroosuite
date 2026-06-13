import tempfile
import os
from unittest.mock import patch, MagicMock

from app.shared.models.core import Domain
from app.shared.db import db


def _setup_test_env(app, account_id, with_caldav=True, with_cache=True):
    paths = {}
    with app.app_context():
        from app.shared.db import db
        from app.shared.models.core import CustomerAccount
        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, account.domain_id)
        if with_caldav:
            domain.caldav_host = "localhost"
            domain.caldav_port = 5232
            domain.caldav_use_tls = False
        if with_cache:
            f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            paths["cache"] = f.name
            f.close()
            account.cache_db_path = paths["cache"]
        db.session.commit()
    return paths


def test_calendar_index_no_caldav_config(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id, with_caldav=False)
    try:
        resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"not configured" in resp.data
    finally:
        if paths.get("cache"):
            os.unlink(paths["cache"])


def test_calendar_index_empty(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events"):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"calendar-grid" in resp.data
        assert b"Sync calendars" in resp.data
        assert b"New event" not in resp.data
    finally:
        os.unlink(paths["cache"])


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
        os.unlink(paths["cache"])


def test_event_new_get(authed_client, app):
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
        assert resp.status_code == 200
        assert b"New Event" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_event_new_post_validation_error(authed_client, app):
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
        resp = client.post("/app/calendar/events/new", data={"summary": "", "dtstart": "", "calendar_id": ""})
        assert resp.status_code == 200
        assert b"required" in resp.data
    finally:
        os.unlink(paths["cache"])


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
            ("evt-1", "/test/evt1.ics", "etag1", 1, "Team Meeting", "2025-01-15T10:00:00+00:00", "2025-01-15T11:00:00+00:00", 0, "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Team Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR"),
        )
        conn.commit()
        event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-1'").fetchone()[0]
        conn.close()

    try:
        resp = client.get(f"/app/calendar/events/{event_id}")
        assert resp.status_code == 200
        assert b"Team Meeting" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_event_detail_not_found(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get("/app/calendar/events/99999")
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


def test_event_detail_shows_user_timezone(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.shared.models.core import CustomerSettings
    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        settings = CustomerSettings(customer_id=user_id, timezone="Australia/Adelaide")
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
            ("evt-tz", "/test/evttz.ics", "etag-tz", 1, "TZ Event", "2026-05-14T12:00:00", "2026-05-14T13:00:00", 0, "Australia/Adelaide",
             "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:TZ Event\r\nEND:VEVENT\r\nEND:VCALENDAR"),
        )
        conn.commit()
        event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-tz'").fetchone()[0]
        conn.close()

    try:
        resp = client.get(f"/app/calendar/events/{event_id}")
        assert resp.status_code == 200
        assert b"Australia/Adelaide" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_event_edit_get(authed_client, app):
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
            ("evt-2", "/test/evt2.ics", "etag2", 1, "Standup", "2025-01-15T09:00:00+00:00", "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Standup\r\nEND:VEVENT\r\nEND:VCALENDAR"),
        )
        conn.commit()
        event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-2'").fetchone()[0]
        conn.close()

    try:
        resp = client.get(f"/app/calendar/events/{event_id}/edit")
        assert resp.status_code == 200
        assert b"Edit Event" in resp.data
        assert b"Standup" in resp.data
    finally:
        os.unlink(paths["cache"])


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
            ("evt-3", "/test/evt3.ics", "etag3", 1, "Delete Me", "2025-01-15T09:00:00+00:00", "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Delete Me\r\nEND:VEVENT\r\nEND:VCALENDAR"),
        )
        conn.commit()
        event_id = conn.execute("SELECT id FROM calendar_events WHERE uid = 'evt-3'").fetchone()[0]
        conn.close()

    try:
        with patch("app.modules.calendar.controllers.events.caldav") as mock_caldav:
            mock_session = MagicMock()
            mock_caldav.discover_calendars.return_value = (mock_session, [])
            mock_caldav.delete_event.return_value = True
            resp = client.post(f"/app/calendar/events/{event_id}/delete")
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


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
            ("evt-api", "/test/api.ics", "e1", 1, "API Event", "2025-01-15T10:00:00+00:00", "2025-01-15T11:00:00+00:00", "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:API Event\r\nEND:VEVENT\r\nEND:VCALENDAR"),
        )
        conn.commit()
        conn.close()

    import json
    try:
        resp = client.get("/app/calendar/api/events?start=2025-01-01T00:00:00&end=2025-12-31T23:59:59")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1
        assert data[0]["summary"] == "API Event"
    finally:
        os.unlink(paths["cache"])


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
            ("evt-search", "/test/search.ics", "e1", 1, "Search Test Event", "2025-01-15T10:00:00+00:00", "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Search Test Event\r\nEND:VEVENT\r\nEND:VCALENDAR"),
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
        os.unlink(paths["cache"])


def test_api_search_too_short(authed_client, app):
    client, user_id, account_id = authed_client
    resp = client.get("/app/calendar/api/search?q=a")
    assert resp.status_code == 200
    import json
    assert json.loads(resp.data) == []


def test_calendar_sync(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events") as mock_sync:
            resp = client.post("/app/calendar/sync")
        assert resp.status_code == 302
        mock_sync.assert_called_once()
    finally:
        os.unlink(paths["cache"])


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
        cal_id = conn.execute("SELECT id FROM calendars WHERE uid = 'cal-tog'").fetchone()[0]
        conn.close()

    try:
        resp = client.post(f"/app/calendar/calendars/{cal_id}/toggle")
        assert resp.status_code == 302

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            row = conn.execute("SELECT is_visible FROM calendars WHERE id = ?", (cal_id,)).fetchone()
            assert row[0] == 0
            conn.close()
    finally:
        os.unlink(paths["cache"])


def test_event_new_no_calendars_shows_error(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get("/app/calendar/events/new")
        assert resp.status_code == 200
        assert b"No calendars available" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_auto_create_default_calendar(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.calendar.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    try:
        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)

            from app.modules.calendar.controllers.views import _sync_calendars_and_events, _get_caldav_config, _get_credentials
            from unittest.mock import patch, MagicMock

            from app.shared.models.core import CustomerAccount
            account_obj = db.session.get(CustomerAccount, account_id)
            config = _get_caldav_config(account_obj)

            with patch("app.modules.calendar.controllers.views.caldav") as mock_caldav, \
                 patch("app.modules.calendar.controllers.views._get_credentials", return_value="test-password"):
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
        os.unlink(paths["cache"])


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
        cal_id = conn.execute("SELECT id FROM calendars WHERE uid = 'cal-def'").fetchone()[0]
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
        os.unlink(paths["cache"])


def test_index_shows_no_calendars_message(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events"):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"calendar-grid" in resp.data
        assert b"Sync calendars" in resp.data
        assert b"New event" not in resp.data
    finally:
        os.unlink(paths["cache"])


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
        cal_id = conn.execute("SELECT id FROM calendars WHERE uid = 'cal-qc'").fetchone()[0]
        conn.close()
    return paths, cal_id


def test_quick_create_no_account(authed_client, app):
    client, user_id, account_id = authed_client
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
        os.unlink(paths["cache"])


def test_quick_create_missing_dtstart(authed_client, app):
    client, user_id, account_id = authed_client
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
        os.unlink(paths["cache"])


def test_quick_create_missing_calendar(authed_client, app):
    client, user_id, account_id = authed_client
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
        os.unlink(paths["cache"])


def test_quick_create_calendar_not_found(authed_client, app):
    client, user_id, account_id = authed_client
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
        os.unlink(paths["cache"])


def test_quick_create_timed_event(authed_client, app):
    client, user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views.caldav") as mock_caldav, \
             patch("app.modules.calendar.controllers.views._get_credentials", return_value="test-password"):
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
            events = conn.execute("SELECT summary, dtstart, dtend, all_day FROM calendar_events").fetchall()
            conn.close()
        assert len(events) == 1
        assert events[0][0] == "Quick Meeting"
        assert events[0][3] == 0
    finally:
        os.unlink(paths["cache"])


def test_quick_create_all_day_event(authed_client, app):
    client, user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views.caldav") as mock_caldav, \
             patch("app.modules.calendar.controllers.views._get_credentials", return_value="test-password"):
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
        os.unlink(paths["cache"])


def test_quick_create_default_summary(authed_client, app):
    client, user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views.caldav") as mock_caldav, \
             patch("app.modules.calendar.controllers.views._get_credentials", return_value="test-password"):
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
            summary = conn.execute("SELECT summary FROM calendar_events").fetchone()[0]
            conn.close()
        assert summary == "(no title)"
    finally:
        os.unlink(paths["cache"])


def test_quick_create_no_caldav_config(authed_client, app):
    client, user_id, account_id = authed_client
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
        os.unlink(paths["cache"])


def test_quick_create_caldav_failure(authed_client, app):
    client, user_id, account_id = authed_client
    paths, cal_id = _setup_calendar_and_cache(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views.caldav") as mock_caldav, \
             patch("app.modules.calendar.controllers.views._get_credentials", return_value="test-password"):
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
        os.unlink(paths["cache"])


def test_quick_create_includes_popover_html(authed_client, app):
    client, user_id, account_id = authed_client
    paths, _ = _setup_calendar_and_cache(app, account_id)
    try:
        with patch("app.modules.calendar.controllers.views._sync_calendars_and_events"):
            resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"quick-create-popover" in resp.data
        assert b"qc-summary" in resp.data
        assert b"qc-calendar" in resp.data
        assert b"qc-save" in resp.data
        assert b"qc-more-options" in resp.data
        assert b"time-cell" in resp.data
        assert b"month-day-cell" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_sync_error_shows_warning_banner(authed_client, app):
    client, user_id, account_id = authed_client
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
        os.unlink(paths["cache"])


def test_sync_success_no_warning_banner(authed_client, app):
    client, user_id, account_id = authed_client
    paths, _ = _setup_calendar_and_cache(app, account_id)
    try:
        resp = client.get("/app/calendar/")
        assert resp.status_code == 200
        assert b"caldav-warning" not in resp.data
    finally:
        if os.path.exists(paths["cache"]):
            os.unlink(paths["cache"])
