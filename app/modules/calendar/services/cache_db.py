import json

import sqlcipher3

from app.modules.calendar.services.cache_migrations import CALENDAR_CACHE_MIGRATIONS
from app.shared.cache_errors import CacheKeyMismatchError
from app.shared.icalendar import parse_icalendar
from app.shared.migrations import run_migrations


def open_cache(db_path, key):
    if not key:
        raise ValueError("cache key required")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _init_schema(conn)
    except (MemoryError, Exception) as exc:
        conn.close()
        import os as _os
        if _os.path.exists(db_path):
            _os.unlink(db_path)
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            _init_schema(conn)
        except Exception:
            conn.close()
            raise CacheKeyMismatchError(
                f"Failed to open cache database even after reset. db_path={db_path}"
            ) from exc
    return conn


def _init_schema(conn):
    run_migrations(conn, CALENDAR_CACHE_MIGRATIONS)


def upsert_calendar(conn, uid, href, displayname="", color="#4285f4", description=None, is_default=False):
    row = conn.execute("SELECT id FROM calendars WHERE uid = ?", (uid,)).fetchone()
    fields = {
        "uid": uid,
        "href": href,
        "displayname": displayname,
        "color": color,
        "description": description,
        "is_default": 1 if is_default else 0,
    }
    if row:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE calendars SET {sets} WHERE id = ?", (*fields.values(), row[0]))
        conn.commit()
        return row[0]
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    conn.execute(f"INSERT INTO calendars ({cols}) VALUES ({placeholders})", tuple(fields.values()))
    conn.commit()
    return conn.execute("SELECT id FROM calendars WHERE uid = ?", (uid,)).fetchone()[0]


def get_all_calendars(conn):
    rows = conn.execute("SELECT * FROM calendars ORDER BY is_default DESC, order_index ASC, displayname ASC").fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM calendars LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def count_calendars(conn):
    row = conn.execute("SELECT COUNT(*) FROM calendars").fetchone()
    return row[0] if row else 0


def get_calendar(conn, calendar_id):
    row = conn.execute("SELECT * FROM calendars WHERE id = ?", (calendar_id,)).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM calendars LIMIT 0").description]
    return dict(zip(cols, row))


def update_calendar(conn, calendar_id, displayname=None, color=None, is_visible=None):
    sets = []
    vals = []
    if displayname is not None:
        sets.append("displayname = ?")
        vals.append(displayname)
    if color is not None:
        sets.append("color = ?")
        vals.append(color)
    if is_visible is not None:
        sets.append("is_visible = ?")
        vals.append(1 if is_visible else 0)
    if not sets:
        return
    vals.append(calendar_id)
    conn.execute(f"UPDATE calendars SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()


def delete_calendar_by_id(conn, calendar_id):
    conn.execute("DELETE FROM calendar_events WHERE calendar_id = ?", (calendar_id,))
    conn.execute("DELETE FROM calendar_state WHERE calendar_href IN (SELECT href FROM calendars WHERE id = ?)", (calendar_id,))
    conn.execute("DELETE FROM calendars WHERE id = ?", (calendar_id,))
    conn.commit()


def delete_calendar_by_uid(conn, uid):
    conn.execute("DELETE FROM calendars WHERE uid = ?", (uid,))
    conn.commit()


def upsert_event(conn, uid, href, etag, calendar_id, ical_text):
    parsed = parse_icalendar(ical_text)
    organizer_str = None
    org = parsed.get("organizer")
    if org:
        organizer_str = json.dumps(org)
    attendees_str = None
    atts = parsed.get("attendees")
    if atts:
        attendees_str = json.dumps(atts)
    exdates_str = None
    exd = parsed.get("exdates")
    if exd:
        exdates_str = json.dumps(exd)
    rdates_str = None
    rd = parsed.get("rdates")
    if rd:
        rdates_str = json.dumps(rd)
    categories_str = None
    cats = parsed.get("categories")
    if cats:
        categories_str = json.dumps(cats)

    row = conn.execute("SELECT id, timezone FROM calendar_events WHERE uid = ? AND calendar_id = ?", (uid, calendar_id)).fetchone()
    existing_tz = row["timezone"] if row else None
    fields = {
        "uid": uid,
        "href": href,
        "etag": etag,
        "calendar_id": calendar_id,
        "summary": parsed.get("summary", ""),
        "description": parsed.get("description"),
        "location": parsed.get("location"),
        "dtstart": parsed.get("dtstart", ""),
        "dtend": parsed.get("dtend"),
        "all_day": 1 if parsed.get("all_day") else 0,
        "rrule": parsed.get("rrule"),
        "exdates": exdates_str,
        "rdates": rdates_str,
        "recurrence_id": parsed.get("recurrence_id"),
        "organizer": organizer_str,
        "attendees": attendees_str,
        "status": parsed.get("status", "CONFIRMED"),
        "categories": categories_str,
        "class": parsed.get("class_", "PUBLIC"),
        "url": parsed.get("url"),
        "timezone": parsed.get("timezone") or existing_tz,
        "sequence": parsed.get("sequence", 0),
        "raw_ical": ical_text,
        "created_at": parsed.get("created_at"),
        "last_modified": parsed.get("last_modified"),
        "updated_at": _now(),
    }

    event_id = None
    if row:
        event_id = row[0]
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE calendar_events SET {sets} WHERE id = ?", (*fields.values(), event_id))
    else:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO calendar_events ({cols}) VALUES ({placeholders})", tuple(fields.values()))
        event_id = cur.lastrowid

    conn.execute("DELETE FROM calendar_reminders WHERE event_id = ?", (event_id,))
    alarms = parsed.get("alarms", [])
    for alarm in alarms:
        conn.execute(
            "INSERT INTO calendar_reminders (event_id, trigger_val, action, description) VALUES (?, ?, ?, ?)",
            (event_id, alarm.get("trigger", "-PT15M"), alarm.get("action", "DISPLAY"), alarm.get("description", "")),
        )
    conn.commit()
    return event_id


def delete_event_by_uid(conn, uid, calendar_id=None):
    if calendar_id:
        conn.execute("DELETE FROM calendar_reminders WHERE event_id IN (SELECT id FROM calendar_events WHERE uid = ? AND calendar_id = ?)", (uid, calendar_id))
        conn.execute("DELETE FROM calendar_events WHERE uid = ? AND calendar_id = ?", (uid, calendar_id))
    else:
        conn.execute("DELETE FROM calendar_reminders WHERE event_id IN (SELECT id FROM calendar_events WHERE uid = ?)", (uid,))
        conn.execute("DELETE FROM calendar_events WHERE uid = ?", (uid,))
    conn.commit()


def get_event(conn, event_id):
    row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM calendar_events LIMIT 0").description]
    event = dict(zip(cols, row))
    event["reminders"] = _get_reminders(conn, event_id)
    return event


def get_event_by_uid(conn, uid, calendar_id=None):
    if calendar_id:
        row = conn.execute("SELECT * FROM calendar_events WHERE uid = ? AND calendar_id = ?", (uid, calendar_id)).fetchone()
    else:
        row = conn.execute("SELECT * FROM calendar_events WHERE uid = ?", (uid,)).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM calendar_events LIMIT 0").description]
    event = dict(zip(cols, row))
    event["reminders"] = _get_reminders(conn, event["id"])
    return event


def get_events_range(conn, start, end, calendar_ids=None):
    has_range = start is not None and end is not None
    if calendar_ids:
        placeholders = ",".join("?" for _ in calendar_ids)
        if has_range:
            rows = conn.execute(
                f"""
                SELECT e.*, c.color as calendar_color, c.displayname as calendar_name
                FROM calendar_events e
                JOIN calendars c ON e.calendar_id = c.id
                WHERE e.calendar_id IN ({placeholders})
                  AND e.dtstart < ? AND (e.dtend > ? OR e.dtend IS NULL)
                  AND c.is_visible = 1
                ORDER BY e.dtstart ASC
                """,
                (*calendar_ids, end, start),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT e.*, c.color as calendar_color, c.displayname as calendar_name
                FROM calendar_events e
                JOIN calendars c ON e.calendar_id = c.id
                WHERE e.calendar_id IN ({placeholders})
                  AND c.is_visible = 1
                ORDER BY e.dtstart ASC
                """,
                tuple(calendar_ids),
            ).fetchall()
    else:
        if has_range:
            rows = conn.execute(
                """
                SELECT e.*, c.color as calendar_color, c.displayname as calendar_name
                FROM calendar_events e
                JOIN calendars c ON e.calendar_id = c.id
                WHERE e.dtstart < ? AND (e.dtend > ? OR e.dtend IS NULL)
                  AND c.is_visible = 1
                ORDER BY e.dtstart ASC
                """,
                (end, start),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT e.*, c.color as calendar_color, c.displayname as calendar_name
                FROM calendar_events e
                JOIN calendars c ON e.calendar_id = c.id
                WHERE c.is_visible = 1
                ORDER BY e.dtstart ASC
                """,
                (),
            ).fetchall()
    if not rows:
        return []
    cols = [desc[0] for desc in conn.execute("SELECT e.*, c.color as calendar_color, c.displayname as calendar_name FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def get_upcoming_events(conn, limit=30):
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = conn.execute(
        """
        SELECT e.*, c.color as calendar_color, c.displayname as calendar_name
        FROM calendar_events e
        JOIN calendars c ON e.calendar_id = c.id
        WHERE e.dtstart >= ? AND c.is_visible = 1
        ORDER BY e.dtstart ASC
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()
    if not rows:
        return []
    cols = [desc[0] for desc in conn.execute("SELECT e.*, c.color as calendar_color, c.displayname as calendar_name FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def count_events(conn, calendar_id=None):
    if calendar_id:
        row = conn.execute("SELECT COUNT(*) FROM calendar_events WHERE calendar_id = ?", (calendar_id,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()
    return row[0] if row else 0


def search_events(conn, query, limit=50):
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT e.*, c.color as calendar_color, c.displayname as calendar_name
        FROM calendar_events e
        JOIN calendars c ON e.calendar_id = c.id
        WHERE (e.summary LIKE ? OR e.description LIKE ? OR e.location LIKE ?)
          AND c.is_visible = 1
        ORDER BY e.dtstart DESC
        LIMIT ?
        """,
        (like, like, like, limit),
    ).fetchall()
    if not rows:
        return []
    cols = [desc[0] for desc in conn.execute("SELECT e.*, c.color as calendar_color, c.displayname as calendar_name FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def search_events_api(conn, query, limit=10):
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT e.uid, e.summary, e.dtstart, e.dtend, e.all_day, e.location, c.color as calendar_color
        FROM calendar_events e
        JOIN calendars c ON e.calendar_id = c.id
        WHERE (e.summary LIKE ? OR e.description LIKE ? OR e.location LIKE ?)
          AND c.is_visible = 1
        ORDER BY e.dtstart DESC
        LIMIT ?
        """,
        (like, like, like, limit),
    ).fetchall()
    results = []
    for row in rows:
        results.append({
            "uid": row["uid"],
            "summary": row["summary"],
            "dtstart": row["dtstart"],
            "dtend": row["dtend"],
            "all_day": bool(row["all_day"]),
            "location": row["location"],
            "calendar_color": row["calendar_color"],
        })
    return results


def get_sync_state(conn, calendar_href):
    row = conn.execute(
        "SELECT sync_token, ctag, last_sync_at FROM calendar_state WHERE calendar_href = ?",
        (calendar_href,),
    ).fetchone()
    if not row:
        return None
    return {"sync_token": row["sync_token"], "ctag": row["ctag"], "last_sync_at": row["last_sync_at"]}


def set_sync_state(conn, calendar_href, sync_token=None, ctag=None):
    conn.execute(
        """
        INSERT INTO calendar_state (calendar_href, sync_token, ctag, last_sync_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(calendar_href) DO UPDATE SET
            sync_token = COALESCE(excluded.sync_token, calendar_state.sync_token),
            ctag = COALESCE(excluded.ctag, calendar_state.ctag),
            last_sync_at = datetime('now')
        """,
        (calendar_href, sync_token, ctag),
    )
    conn.commit()


def _get_reminders(conn, event_id):
    rows = conn.execute("SELECT * FROM calendar_reminders WHERE event_id = ?", (event_id,)).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM calendar_reminders LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def set_event_source_email(conn, event_id, message_id, account_id):
    conn.execute(
        "UPDATE calendar_events SET source_email_message_id = ?, source_email_account_id = ? WHERE id = ?",
        (message_id, account_id, event_id),
    )
    conn.commit()


def get_conflicting_events(conn, start, end, exclude_event_id=None, calendar_ids=None):
    if calendar_ids:
        placeholders = ",".join("?" for _ in calendar_ids)
        rows = conn.execute(
            f"""
            SELECT e.id, e.summary, e.dtstart, e.dtend, e.all_day, e.calendar_id
            FROM calendar_events e
            WHERE e.dtstart < ? AND (e.dtend > ? OR e.dtend IS NULL)
              AND e.status != 'CANCELLED'
              AND e.calendar_id IN ({placeholders})
            ORDER BY e.dtstart ASC
            """,
            (end, start, *calendar_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT e.id, e.summary, e.dtstart, e.dtend, e.all_day, e.calendar_id
            FROM calendar_events e
            WHERE e.dtstart < ? AND (e.dtend > ? OR e.dtend IS NULL)
              AND e.status != 'CANCELLED'
            ORDER BY e.dtstart ASC
            """,
            (end, start),
        ).fetchall()
    results = []
    for row in rows:
        if exclude_event_id and row["id"] == exclude_event_id:
            continue
        results.append({
            "id": row["id"],
            "summary": row["summary"],
            "dtstart": row["dtstart"],
            "dtend": row["dtend"],
            "all_day": bool(row["all_day"]),
            "calendar_id": row["calendar_id"],
        })
    return results
