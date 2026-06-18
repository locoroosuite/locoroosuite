import tempfile
import os
import json

from app.modules.calendar.services.cache_db import open_cache


def _make_cache():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = f.name
    f.close()
    key = "0" * 64
    conn = open_cache(path, key)
    return conn, path, key


def test_open_cache_creates_tables():
    conn, path, key = _make_cache()
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "calendars" in tables
        assert "calendar_events" in tables
        assert "calendar_reminders" in tables
        assert "calendar_state" in tables
    finally:
        conn.close()
        os.unlink(path)


def test_upsert_and_get_calendar():
    conn, path, key = _make_cache()
    try:
        cal_id = conn.execute("SELECT id FROM calendars WHERE uid = 'cal1'").fetchone()
        assert cal_id is None

        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "cal1", "/cals/cal1/", "Work", "#ff0000")
        assert cid > 0

        cal = cache_db.get_calendar(conn, cid)
        assert cal["displayname"] == "Work"
        assert cal["color"] == "#ff0000"
        assert cal["href"] == "/cals/cal1/"

        cache_db.upsert_calendar(conn, "cal1", "/cals/cal1/", "Personal", "#00ff00")
        cal2 = cache_db.get_calendar(conn, cid)
        assert cal2["displayname"] == "Personal"
        assert cal2["color"] == "#00ff00"
    finally:
        conn.close()
        os.unlink(path)


def test_get_all_calendars():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cache_db.upsert_calendar(conn, "c1", "/c1/", "Cal 1", "#ff0000")
        cache_db.upsert_calendar(conn, "c2", "/c2/", "Cal 2", "#00ff00")
        cals = cache_db.get_all_calendars(conn)
        assert len(cals) == 2
    finally:
        conn.close()
        os.unlink(path)


def test_update_calendar():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Old Name", "#ff0000")
        cache_db.update_calendar(conn, cid, displayname="New Name", color="#0000ff", is_visible=False)
        cal = cache_db.get_calendar(conn, cid)
        assert cal["displayname"] == "New Name"
        assert cal["color"] == "#0000ff"
        assert cal["is_visible"] == 0
    finally:
        conn.close()
        os.unlink(path)


def test_delete_calendar():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Delete Me", "#ff0000")
        cache_db.delete_calendar_by_id(conn, cid)
        assert cache_db.get_calendar(conn, cid) is None
    finally:
        conn.close()
        os.unlink(path)


def test_upsert_and_get_event():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt1\r\nSUMMARY:Meeting\r\nDTSTART:20250115T100000Z\r\nDTEND:20250115T110000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        eid = cache_db.upsert_event(conn, "evt1", "/c1/evt1.ics", "etag1", cid, ical)
        assert eid > 0

        event = cache_db.get_event(conn, eid)
        assert event["summary"] == "Meeting"
        assert event["uid"] == "evt1"

        event_by_uid = cache_db.get_event_by_uid(conn, "evt1")
        assert event_by_uid is not None
        assert event_by_uid["summary"] == "Meeting"
    finally:
        conn.close()
        os.unlink(path)


def test_delete_event():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-del\r\nSUMMARY:Delete Me\r\nDTSTART:20250115T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        cache_db.upsert_event(conn, "evt-del", "/c1/evt-del.ics", "etag", cid, ical)

        cache_db.delete_event_by_uid(conn, "evt-del")
        assert cache_db.get_event_by_uid(conn, "evt-del") is None
    finally:
        conn.close()
        os.unlink(path)


def test_get_events_range():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical1 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-r1\r\nSUMMARY:Event 1\r\nDTSTART:20250115T100000Z\r\nDTEND:20250115T110000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        ical2 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-r2\r\nSUMMARY:Event 2\r\nDTSTART:20250215T100000Z\r\nDTEND:20250215T110000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        cache_db.upsert_event(conn, "evt-r1", "/c1/e1.ics", "e1", cid, ical1)
        cache_db.upsert_event(conn, "evt-r2", "/c1/e2.ics", "e2", cid, ical2)

        events = cache_db.get_events_range(conn, "2025-01-01T00:00:00", "2025-01-31T23:59:59")
        assert len(events) == 1
        assert events[0]["summary"] == "Event 1"

        events2 = cache_db.get_events_range(conn, "2025-01-01T00:00:00", "2025-12-31T23:59:59")
        assert len(events2) == 2
    finally:
        conn.close()
        os.unlink(path)


def test_get_events_range_no_date_filter():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical1 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-nf1\r\nSUMMARY:Event A\r\nDTSTART:20250115T100000Z\r\nDTEND:20250115T110000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        ical2 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-nf2\r\nSUMMARY:Event B\r\nDTSTART:20250215T100000Z\r\nDTEND:20250215T110000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        cache_db.upsert_event(conn, "evt-nf1", "/c1/a.ics", "e1", cid, ical1)
        cache_db.upsert_event(conn, "evt-nf2", "/c1/b.ics", "e2", cid, ical2)

        events = cache_db.get_events_range(conn, None, None)
        assert len(events) == 2

        events_cal = cache_db.get_events_range(conn, None, None, calendar_ids=[cid])
        assert len(events_cal) == 2

        events_cal_bad = cache_db.get_events_range(conn, None, None, calendar_ids=[9999])
        assert len(events_cal_bad) == 0
    finally:
        conn.close()
        os.unlink(path)


def test_count_events():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-cnt\r\nSUMMARY:Count\r\nDTSTART:20250115T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        cache_db.upsert_event(conn, "evt-cnt", "/c1/evt.ics", "e", cid, ical)

        assert cache_db.count_events(conn) == 1
        assert cache_db.count_events(conn, cid) == 1
        assert cache_db.count_events(conn, 9999) == 0
    finally:
        conn.close()
        os.unlink(path)


def test_search_events():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-search\r\nSUMMARY:Searchable Meeting\r\nDESCRIPTION:In Room 42\r\nLOCATION:Building A\r\nDTSTART:20250115T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"
        cache_db.upsert_event(conn, "evt-search", "/c1/s.ics", "e", cid, ical)

        results = cache_db.search_events(conn, "Searchable")
        assert len(results) >= 1
        assert results[0]["summary"] == "Searchable Meeting"

        results2 = cache_db.search_events(conn, "Building")
        assert len(results2) >= 1
    finally:
        conn.close()
        os.unlink(path)


def test_sync_state():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        assert cache_db.get_sync_state(conn, "/cals/c1/") is None

        cache_db.set_sync_state(conn, "/cals/c1/", sync_token="token-1", ctag="ctag-1")
        state = cache_db.get_sync_state(conn, "/cals/c1/")
        assert state["sync_token"] == "token-1"
        assert state["ctag"] == "ctag-1"
        assert state["last_sync_at"] is not None

        cache_db.set_sync_state(conn, "/cals/c1/", sync_token="token-2")
        state2 = cache_db.get_sync_state(conn, "/cals/c1/")
        assert state2["sync_token"] == "token-2"
        assert state2["ctag"] == "ctag-1"
    finally:
        conn.close()
        os.unlink(path)


def test_event_with_alarm():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-alarm\r\nSUMMARY:With Alarm\r\nDTSTART:20250115T100000Z\r\nBEGIN:VALARM\r\nTRIGGER:-PT15M\r\nACTION:DISPLAY\r\nDESCRIPTION:Soon\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR"
        eid = cache_db.upsert_event(conn, "evt-alarm", "/c1/a.ics", "e", cid, ical)

        event = cache_db.get_event(conn, eid)
        assert len(event["reminders"]) == 1
        assert event["reminders"][0]["trigger_val"] == "-PT15M"
        assert event["reminders"][0]["action"] == "DISPLAY"
    finally:
        conn.close()
        os.unlink(path)


def test_upsert_event_preserves_timezone_on_sync():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical_with_tz = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-tz-sync\r\nSUMMARY:Adelaide Event\r\n"
            "DTSTART;TZID=Australia/Adelaide:20260514T120000\r\n"
            "DTEND;TZID=Australia/Adelaide:20260514T130000\r\n"
            "END:VEVENT\r\nEND:VCALENDAR"
        )
        eid = cache_db.upsert_event(conn, "evt-tz-sync", "/c1/tz.ics", "e1", cid, ical_with_tz)
        event = cache_db.get_event(conn, eid)
        assert event["timezone"] == "Australia/Adelaide"

        ical_utc_sync = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-tz-sync\r\nSUMMARY:Adelaide Event\r\n"
            "DTSTART:20260514T023000Z\r\n"
            "DTEND:20260514T033000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR"
        )
        cache_db.upsert_event(conn, "evt-tz-sync", "/c1/tz.ics", "e2", cid, ical_utc_sync)
        event_after_sync = cache_db.get_event(conn, eid)
        assert event_after_sync["timezone"] == "Australia/Adelaide"
    finally:
        conn.close()
        os.unlink(path)


def test_search_events_api_shape():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-sapi\r\nSUMMARY:Search Meeting\r\n"
            "LOCATION:Conf Room\r\nDTSTART:20250115T100000Z\r\nDTEND:20250115T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR"
        )
        cache_db.upsert_event(conn, "evt-sapi", "/c1/s.ics", "e", cid, ical)

        results = cache_db.search_events_api(conn, "Search")
        assert len(results) == 1
        row = results[0]
        assert set(row.keys()) == {"uid", "summary", "dtstart", "dtend", "all_day", "location", "calendar_color"}
        assert row["uid"] == "evt-sapi"
        assert row["summary"] == "Search Meeting"
        assert row["location"] == "Conf Room"
        assert row["all_day"] is False
        assert row["calendar_color"] == "#4285f4"
        assert row["dtstart"]
        assert row["dtend"]
    finally:
        conn.close()
        os.unlink(path)


def test_get_conflicting_events_shape():
    conn, path, key = _make_cache()
    try:
        from app.modules.calendar.services import cache_db
        cid = cache_db.upsert_calendar(conn, "c1", "/c1/", "Work", "#4285f4")

        ical = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-conf\r\nSUMMARY:Busy Block\r\n"
            "DTSTART:20250115T100000Z\r\nDTEND:20250115T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR"
        )
        cache_db.upsert_event(conn, "evt-conf", "/c1/conf.ics", "e", cid, ical)

        conflicts = cache_db.get_conflicting_events(conn, "2025-01-15T09:00:00", "2025-01-15T10:30:00")
        assert len(conflicts) == 1
        row = conflicts[0]
        assert set(row.keys()) == {"id", "summary", "dtstart", "dtend", "all_day", "calendar_id"}
        assert row["summary"] == "Busy Block"
        assert row["all_day"] is False
        assert row["calendar_id"] == cid

        excluded = cache_db.get_conflicting_events(
            conn, "2025-01-15T09:00:00", "2025-01-15T10:30:00", exclude_event_id=row["id"]
        )
        assert excluded == []
    finally:
        conn.close()
        os.unlink(path)
