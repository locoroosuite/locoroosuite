"""Shared calendar event → API payload mapping (U15.56).

Used by both the REST controller and the MCP tool so the read shape
(reminders echo, attendees, recurrence, audit timestamps) can never drift
between surfaces.
"""

from __future__ import annotations

from typing import Any

from app.modules.calendar.services.reminders import alarms_to_reminders


def event_to_payload(row) -> dict[str, Any]:
    """Map a calendar_events cache row to the full-detail API payload."""
    from app.shared.icalendar import parse_icalendar

    # sqlite3.Row iteration yields values, not keys (SIM118 does not apply).
    d = {k: row[k] for k in row.keys()} if not isinstance(row, dict) else dict(row)  # noqa: SIM118
    ical_raw = d.get("raw_ical") or d.get("ical_text")
    parsed: dict[str, Any] = {}
    if ical_raw:
        parsed = parse_icalendar(ical_raw) or {}
    return {
        "id": d["id"],
        "uid": d.get("uid"),
        "summary": parsed.get("summary", "") or d.get("summary", ""),
        "description": parsed.get("description", "") or d.get("description", ""),
        "location": parsed.get("location", "") or d.get("location", ""),
        "start": parsed.get("dtstart") or d.get("dtstart"),
        "end": parsed.get("dtend") or d.get("dtend"),
        "is_all_day": parsed.get("all_day")
        if parsed.get("all_day") is not None
        else bool(d.get("all_day")),
        "status": parsed.get("status", "") or d.get("status", ""),
        "calendar_id": d.get("calendar_id"),
        # U15.56: reminders echo back as {type, trigger_minutes} (issue #11),
        # plus attendees, recurrence, and audit timestamps.
        "reminders": alarms_to_reminders(parsed.get("alarms")),
        "attendees": parsed.get("attendees", []),
        "recurrence": parsed.get("rrule"),
        "categories": parsed.get("categories", []),
        "created": parsed.get("created_at"),
        "last_modified": parsed.get("last_modified"),
    }
