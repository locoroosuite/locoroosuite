from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from flask import Flask
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from app.mcp.auth import McpAuthError
from app.mcp.errors import resilient_tool
from app.mcp.helpers import err, ok, ok_paginated, resolve_read, resolve_write
from app.mcp.schemas import EventAttendee, EventReminder
from app.mcp.tools.mail import _ServiceConnectionError

_AccId = Annotated[int | None, Field(description="Account ID (uses default account if omitted)")]

_cal_logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _get_cache_conn(account_id, dek, flask_app):
    from app.shared.models.core import CustomerAccount
    from app.shared.db import db
    from app.modules.calendar.services.cache import get_cache_path
    from app.modules.calendar.services.cache_db import open_cache
    account = db.session.get(CustomerAccount, account_id)
    if not account:
        raise McpAuthError("NOT_FOUND", f"Account {account_id} not found")
    path = get_cache_path(account)
    return open_cache(path, dek)


def _calendar_to_dict(row):
    d = _row_to_dict(row) if not isinstance(row, dict) else row
    return {
        "id": d["id"],
        "uid": d.get("uid"),
        "name": d.get("displayname", ""),
        "color": d.get("color", "#4285f4"),
        "is_default": bool(d.get("is_default")),
    }


def _event_to_dict(row):
    d = _row_to_dict(row) if not isinstance(row, dict) else row
    from app.shared.icalendar import parse_icalendar
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
        "is_all_day": parsed.get("is_all_day", False) if parsed.get("is_all_day") is not None else bool(d.get("all_day")),
        "status": parsed.get("status", "") or d.get("status", ""),
        "calendar_id": d.get("calendar_id"),
    }


def _get_caldav_session(account, dek, flask_app):
    from app.shared.models.core import Domain
    from app.shared.db import db
    from app.modules.mail.services.secrets import decrypt_with_key
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.caldav_host:
        raise McpAuthError("NOT_CONFIGURED", "CalDAV is not configured for this domain")
    scheme = "https" if domain.caldav_use_tls else "http"
    base_url = f"{scheme}://{domain.caldav_host}:{domain.caldav_port or 5232}"
    try:
        password = decrypt_with_key(account.encrypted_secret, dek) if account.encrypted_secret else ""
    except Exception as exc:
        raise McpAuthError(
            "DEK_MISMATCH",
            "Your encryption key does not match the stored credentials. "
            "Reset your API access: go to Settings \u2192 API \u2192 Disable, then re-enable and create a new token.",
        ) from exc
    try:
        from app.modules.calendar.services import caldav
        s, calendars = caldav.discover_calendars(base_url, account.username, password)
        return s, calendars, base_url, password
    except McpAuthError:
        raise
    except Exception as exc:
        raise _ServiceConnectionError("CalDAV", base_url, exc) from exc


def register(mcp: FastMCP, flask_app: Flask) -> None:
    @mcp.tool(
        name="calendar_list_calendars",
        title="List Calendars",
        description="List all calendars for the authenticated account. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_list_calendars(account_id: _AccId = None) -> str:
        ctx, aid, dek = resolve_read(flask_app, "calendar", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_all_calendars
                rows = get_all_calendars(conn)
                items = [_calendar_to_dict(r) for r in rows]
            finally:
                conn.close()
        return ok(items)

    @mcp.tool(
        name="calendar_create_calendar",
        title="Create Calendar",
        description="Create a new calendar via CalDAV.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_create_calendar(
        name: Annotated[str, Field(description="Calendar name")],
        color: Annotated[str | None, Field(description="Calendar color as hex (e.g. '#3a87ad')")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "calendar", account_id)
        cal_color = color or "#4285f4"
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            try:
                s, _, base_url, _ = _get_caldav_session(account, dek, flask_app)
                from app.modules.calendar.services import caldav
                cal_url = caldav.create_calendar(s, base_url, account.username, name, cal_color)
            except McpAuthError:
                raise
            except Exception as exc:
                return err("CALDAV_ERROR", f"CalDAV operation failed: {exc}")
            cal_uid = uuid.uuid4().hex
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import upsert_calendar, get_calendar
                cal_db_id = upsert_calendar(conn, cal_uid, cal_url, displayname=name, color=cal_color)
                cal_row = get_calendar(conn, cal_db_id)
                result = _calendar_to_dict(cal_row) if cal_row else {"uid": cal_uid, "name": name, "color": cal_color}
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "calendar", "calendar_created", {"account_id": aid, "uid": cal_uid})
        return ok(result)

    @mcp.tool(
        name="calendar_update_calendar",
        title="Update Calendar",
        description="Update a calendar's name and/or color.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_update_calendar(
        calendar_id: Annotated[int, Field(description="ID of the calendar to update")],
        name: Annotated[str | None, Field(description="New calendar name")] = None,
        color: Annotated[str | None, Field(description="New calendar color as hex")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "calendar", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_calendar, update_calendar as db_update
                cal = get_calendar(conn, calendar_id)
                if not cal:
                    return err("NOT_FOUND", "Calendar not found")
                cal = _row_to_dict(cal)
                db_update(conn, calendar_id, displayname=name, color=color)
            finally:
                conn.close()
            try:
                s, _, _, _ = _get_caldav_session(account, dek, flask_app)
                from app.modules.calendar.services import caldav
                caldav.update_calendar_props(s, cal.get("href", ""), displayname=name, color=color)
            except Exception:
                _cal_logger.warning("CalDAV prop update failed for calendar_id=%s", calendar_id, exc_info=True)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                cal_row = get_calendar(conn, calendar_id)
                result = _calendar_to_dict(cal_row) if cal_row else {"id": calendar_id}
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "calendar", "calendar_updated", {"account_id": aid, "calendar_id": calendar_id})
        return ok(result)

    @mcp.tool(
        name="calendar_delete_calendar",
        title="Delete Calendar",
        description="Delete a calendar and all its events.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True),
    )
    @resilient_tool
    async def calendar_delete_calendar(
        calendar_id: Annotated[int, Field(description="ID of the calendar to delete")],
        confirm: Annotated[bool, Field(description="Set to true to confirm deletion of calendar and all events")] = False,
        account_id: _AccId = None,
    ) -> str:
        if not confirm:
            return err("VALIDATION_ERROR", "Confirmation required: set confirm=true")
        ctx, aid, dek = resolve_write(flask_app, "calendar", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_calendar, delete_calendar_by_id
                cal = get_calendar(conn, calendar_id)
                if not cal:
                    return err("NOT_FOUND", "Calendar not found")
                delete_calendar_by_id(conn, calendar_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "calendar", "calendar_deleted", {"account_id": aid, "calendar_id": calendar_id})
        return ok()

    @mcp.tool(
        name="calendar_list_events",
        title="List Events",
        description="List events in a calendar with optional date range filter. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_list_events(
        calendar_id: Annotated[int, Field(description="ID of the calendar to list events from")],
        since: Annotated[str | None, Field(description="ISO 8601 datetime — start of date range")] = None,
        until: Annotated[str | None, Field(description="ISO 8601 datetime — end of date range")] = None,
        max_results: Annotated[int | None, Field(description="Maximum number of events to return (1–200, default 50)", ge=1, le=200)] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "calendar", account_id)
        limit = max_results or 50
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_calendar, get_events_range
                cal = get_calendar(conn, calendar_id)
                if not cal:
                    return err("NOT_FOUND", "Calendar not found")
                if since and until:
                    rows = get_events_range(conn, since, until, calendar_ids=[calendar_id])
                else:
                    rows = get_events_range(conn, None, None, calendar_ids=[calendar_id])
            finally:
                conn.close()
        items = [_event_to_dict(r) for r in rows[:limit]]
        has_more = len(rows) > limit
        return ok_paginated(items, has_more=has_more)

    @mcp.tool(
        name="calendar_get_event",
        title="Get Event",
        description="Get full details of a specific calendar event. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_get_event(
        event_id: Annotated[int, Field(description="ID of the event to retrieve")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "calendar", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_event
                row = get_event(conn, event_id)
                if not row:
                    return err("NOT_FOUND", "Event not found")
            finally:
                conn.close()
        return ok(_event_to_dict(row))

    @mcp.tool(
        name="calendar_search_events",
        title="Search Events",
        description="Search calendar events by query string with optional date range. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_search_events(
        q: Annotated[str, Field(description="Search query string (matched against event summary)")],
        since: Annotated[str | None, Field(description="ISO 8601 datetime — start of date range")] = None,
        until: Annotated[str | None, Field(description="ISO 8601 datetime — end of date range")] = None,
        max_results: Annotated[int | None, Field(description="Maximum number of results to return (1–200, default 50)", ge=1, le=200)] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "calendar", account_id)
        limit = max_results or 50
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import search_events
                rows = search_events(conn, q, limit=limit)
            finally:
                conn.close()
        return ok_paginated([_event_to_dict(r) for r in rows])

    @mcp.tool(
        name="calendar_create_event",
        title="Create Event",
        description="Create a new calendar event.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_create_event(
        calendar_id: Annotated[int, Field(description="ID of the calendar to create the event in")],
        summary: Annotated[str, Field(description="Event title/summary")],
        start: Annotated[str, Field(description="Start time as ISO 8601 with timezone")],
        end: Annotated[str, Field(description="End time as ISO 8601 with timezone")],
        description: Annotated[str | None, Field(description="Event description/body")] = None,
        location: Annotated[str | None, Field(description="Event location")] = None,
        is_all_day: Annotated[bool | None, Field(description="Whether this is an all-day event")] = None,
        attendees: Annotated[list[EventAttendee] | None, Field(description="Array of attendee objects with email, cn, role, partstat, rsvp")] = None,
        reminders: Annotated[list[EventReminder] | None, Field(description="Array of reminder objects with type and trigger_minutes")] = None,
        recurrence: Annotated[str | None, Field(description="RRULE string for recurrence")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "calendar", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_calendar
                cal = get_calendar(conn, calendar_id)
                if not cal:
                    return err("NOT_FOUND", "Calendar not found")
                cal = _row_to_dict(cal)
            finally:
                conn.close()
            from app.shared.icalendar import generate_icalendar, extract_uid
            event_data: dict[str, Any] = {
                "uid": uuid.uuid4().hex,
                "summary": summary,
                "description": description or "",
                "location": location or "",
                "dtstart": start,
                "dtend": end,
                "is_all_day": is_all_day or False,
                "attendees": [a.model_dump(exclude_none=True) for a in (attendees or [])],
                "alarms": [],
            }
            for r in (reminders or []):
                event_data["alarms"].append({"action": r.type or "DISPLAY", "trigger": r.trigger_minutes or "-PT15M"})
            if recurrence:
                event_data["rrule"] = recurrence
            ical_text = generate_icalendar(event_data, uid=event_data["uid"])
            uid = extract_uid(ical_text)
            try:
                s, _, _, _ = _get_caldav_session(account, dek, flask_app)
                from app.modules.calendar.services import caldav
                href, etag = caldav.create_event(s, cal["href"], ical_text, uid=uid)
            except McpAuthError:
                raise
            except Exception as exc:
                return err("CALDAV_ERROR", f"CalDAV operation failed: {exc}")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import upsert_event, get_event_by_uid
                upsert_event(conn, uid, href, etag, calendar_id, ical_text)
                evt_row = get_event_by_uid(conn, uid, calendar_id=calendar_id)
                result = _event_to_dict(evt_row) if evt_row else {"uid": uid, "summary": summary}
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "calendar", "event_created", {"account_id": aid, "uid": uid})
        return ok(result)

    @mcp.tool(
        name="calendar_update_event",
        title="Update Event",
        description="Update an existing calendar event.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_update_event(
        event_id: Annotated[int, Field(description="ID of the event to update")],
        summary: Annotated[str | None, Field(description="New event title/summary")] = None,
        start: Annotated[str | None, Field(description="New start time as ISO 8601 with timezone")] = None,
        end: Annotated[str | None, Field(description="New end time as ISO 8601 with timezone")] = None,
        description: Annotated[str | None, Field(description="New event description")] = None,
        location: Annotated[str | None, Field(description="New event location")] = None,
        is_all_day: Annotated[bool | None, Field(description="Whether this is an all-day event")] = None,
        attendees: Annotated[list[EventAttendee] | None, Field(description="Replacement array of attendee objects with email, cn, role, partstat, rsvp")] = None,
        reminders: Annotated[list[EventReminder] | None, Field(description="Replacement array of reminder objects with type and trigger_minutes")] = None,
        recurrence: Annotated[str | None, Field(description="New RRULE string for recurrence")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "calendar", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_event
                row = get_event(conn, event_id)
                if not row:
                    return err("NOT_FOUND", "Event not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            from app.shared.icalendar import parse_icalendar, generate_icalendar
            parsed: dict[str, Any] = {}
            ical_raw = d.get("raw_ical") or d.get("ical_text")
            if ical_raw:
                parsed = parse_icalendar(ical_raw) or {}
            event_data: dict[str, Any] = {
                "uid": d.get("uid"),
                "summary": summary if summary is not None else parsed.get("summary", ""),
                "description": description if description is not None else parsed.get("description", ""),
                "location": location if location is not None else parsed.get("location", ""),
                "dtstart": start if start is not None else parsed.get("dtstart"),
                "dtend": end if end is not None else parsed.get("dtend"),
                "is_all_day": is_all_day if is_all_day is not None else parsed.get("is_all_day", False),
                "attendees": [a.model_dump(exclude_none=True) for a in attendees] if attendees is not None else parsed.get("attendees", []),
                "sequence": parsed.get("sequence", 0) + 1,
                "alarms": parsed.get("alarms", []),
            }
            if recurrence is not None:
                event_data["rrule"] = recurrence
            elif parsed.get("rrule"):
                event_data["rrule"] = parsed["rrule"]
            ical_text = generate_icalendar(event_data, uid=event_data["uid"])
            calendar_id = d.get("calendar_id")
            try:
                s, _, _, _ = _get_caldav_session(account, dek, flask_app)
                from app.modules.calendar.services import caldav
                href = d.get("href")
                if href:
                    caldav.update_event(s, href, ical_text, d.get("etag"))
                else:
                    conn2 = _get_cache_conn(aid, dek, flask_app)
                    from app.modules.calendar.services.cache_db import get_calendar as _get_cal
                    cal = _row_to_dict(_get_cal(conn2, calendar_id))
                    conn2.close()
                    href, etag = caldav.create_event(s, cal["href"], ical_text, uid=event_data["uid"])
            except McpAuthError:
                raise
            except Exception as exc:
                return err("CALDAV_ERROR", f"CalDAV operation failed: {exc}")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import upsert_event, get_event_by_uid
                upsert_event(conn, event_data["uid"], href or d.get("href"), d.get("etag"), calendar_id, ical_text)
                evt_row = get_event_by_uid(conn, event_data["uid"], calendar_id=calendar_id)
                result = _event_to_dict(evt_row) if evt_row else {"uid": event_data["uid"], "summary": event_data["summary"]}
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "calendar", "event_updated", {"account_id": aid, "uid": event_data["uid"]})
        return ok(result)

    @mcp.tool(
        name="calendar_delete_event",
        title="Delete Event",
        description="Delete a calendar event.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True),
    )
    @resilient_tool
    async def calendar_delete_event(
        event_id: Annotated[int, Field(description="ID of the event to delete")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "calendar", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_event
                row = get_event(conn, event_id)
                if not row:
                    return err("NOT_FOUND", "Event not found")
                d = _row_to_dict(row)
                uid = d.get("uid")
                cal_id = d.get("calendar_id")
            finally:
                conn.close()
            try:
                s, _, _, _ = _get_caldav_session(account, dek, flask_app)
                from app.modules.calendar.services import caldav
                if d.get("href"):
                    caldav.delete_event(s, d["href"], d.get("etag"))
            except Exception:
                _cal_logger.warning("CalDAV event delete failed for uid=%s href=%s", uid, d.get("href"), exc_info=True)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import delete_event_by_uid
                delete_event_by_uid(conn, uid, calendar_id=cal_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "calendar", "event_deleted", {"account_id": aid, "uid": uid})
        return ok()

    @mcp.tool(
        name="calendar_check_free_busy",
        title="Check Free/Busy",
        description="Check for conflicting events in a time range. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def calendar_check_free_busy(
        calendar_ids: Annotated[list[int], Field(description="Array of calendar IDs to check")],
        start: Annotated[str, Field(description="Range start as ISO 8601")],
        end: Annotated[str, Field(description="Range end as ISO 8601")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "calendar", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.calendar.services.cache_db import get_conflicting_events
                rows = get_conflicting_events(conn, start, end, calendar_ids=calendar_ids)
                busy = []
                for r in rows:
                    evt = _event_to_dict(r)
                    entry: dict[str, Any] = {"start": evt["start"], "end": evt["end"], "summary": evt["summary"]}
                    if "calendar_id" in evt:
                        entry["calendar_id"] = evt["calendar_id"]
                    busy.append(entry)
            finally:
                conn.close()
        return ok(busy)
