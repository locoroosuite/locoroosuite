from __future__ import annotations

import uuid as _uuid
from typing import Any

from flask import g

from app.api.openapi import create_api_blueprint
from app.api.schemas.common import ErrorResponse, AccountIdQuery
from app.api.schemas.calendar import (
    CalendarItem, CalendarListResponse, CalendarPath,
    DeleteCalendarBody, CreateCalendarBody, UpdateCalendarBody,
    EventItem, EventListResponse, EventDetailResponse, EventPath,
    ListEventsQuery, SearchEventsQuery, CreateEventBody, UpdateEventBody,
    FreeBusyBody, BusyEntry, FreeBusyResponse,
)
from app.api.controllers.helpers import (
    api_response, api_paginated, api_error, require_api_token, require_scope,
    get_api_account_id, ApiError,
)
from app.shared.models.core import CustomerAccount
from app.modules.calendar.services.cache import get_cache_path
from app.modules.calendar.services.cache_db import (
    open_cache, get_all_calendars, get_calendar,
    delete_calendar_by_id, get_event, get_events_range,
    search_events as db_search_events, count_events,
    get_conflicting_events,
)
from app.shared.icalendar import parse_icalendar, generate_icalendar, extract_uid
from app.shared.ui_events import push_ui_event

bp = create_api_blueprint("calendar", "Calendar management")


def _row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _get_cache_conn(account_id, dek):
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        raise ApiError("NOT_FOUND", "Account not found", 404)
    path = get_cache_path(account)
    return open_cache(path, dek)


def _calendar_to_dict(row):
    d = _row_to_dict(row)
    return {
        "id": d["id"],
        "uid": d.get("uid"),
        "name": d.get("displayname", ""),
        "color": d.get("color", "#4285f4"),
        "is_default": bool(d.get("is_default")),
    }


def _event_to_dict(row):
    d = _row_to_dict(row)
    ical_raw = d.get("raw_ical") or d.get("ical_text")
    parsed = {}
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


@bp.get(
    "/calendar/calendars",
    summary="List calendars",
    description="Returns all calendars for the authenticated account. Requires `calendar:read` scope.",
    responses={"200": CalendarListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:read"])
@require_scope("calendar", "read")
def api_list_calendars(query: AccountIdQuery):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        rows = get_all_calendars(conn)
        items = [_calendar_to_dict(r) for r in rows]
        return api_response(items)
    finally:
        conn.close()


@bp.delete(
    "/calendar/calendars/<int:calendar_id>",
    summary="Delete calendar",
    description="Deletes a calendar by ID from the local cache. Confirmation body `{\"confirm\": true}` is required. Requires `calendar:write` scope.",
    responses={"200": CalendarListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:write"])
@require_scope("calendar", "write")
def api_delete_calendar(path: CalendarPath, body: DeleteCalendarBody):
    calendar_id = path.calendar_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    if not body.confirm:
        return api_error("VALIDATION_ERROR", "Confirmation required: {\"confirm\": true}", 400)
    conn = _get_cache_conn(account_id, dek)
    try:
        cal = get_calendar(conn, calendar_id)
        if not cal:
            return api_error("NOT_FOUND", "Calendar not found", 404)
        delete_calendar_by_id(conn, calendar_id)
        push_ui_event(g.api_context["customer_id"], "calendar", "calendar_deleted", {"account_id": account_id, "calendar_id": calendar_id})
        return api_response(None, 204)
    finally:
        conn.close()


@bp.get(
    "/calendar/calendars/<int:calendar_id>/events",
    summary="List calendar events",
    description="Returns events for a specific calendar, optionally filtered by date range. Requires `calendar:read` scope.",
    responses={"200": EventListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:read"])
@require_scope("calendar", "read")
def api_list_events(path: CalendarPath, query: ListEventsQuery):
    calendar_id = path.calendar_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    limit = min(query.max_results, 200)
    since = query.since
    until = query.until
    conn = _get_cache_conn(account_id, dek)
    try:
        cal = get_calendar(conn, calendar_id)
        if not cal:
            return api_error("NOT_FOUND", "Calendar not found", 404)
        if since and until:
            rows = get_events_range(conn, since, until, calendar_ids=[calendar_id])
        else:
            rows = get_events_range(conn, None, None, calendar_ids=[calendar_id])
        items = [_event_to_dict(r) for r in rows[:limit]]
        has_more = len(rows) > limit
        return api_paginated(items, has_more=has_more)
    finally:
        conn.close()


@bp.get(
    "/calendar/events/<int:event_id>",
    summary="Get event detail",
    description="Returns a single calendar event by ID. Requires `calendar:read` scope.",
    responses={"200": EventDetailResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:read"])
@require_scope("calendar", "read")
def api_get_event(path: EventPath):
    event_id = path.event_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_event(conn, event_id)
        if not row:
            return api_error("NOT_FOUND", "Event not found", 404)
        result = _event_to_dict(row)
        return api_response(result)
    finally:
        conn.close()


@bp.get(
    "/calendar/search",
    summary="Search events",
    description="Full-text search across all calendar events. Returns matching events sorted by relevance. Requires `calendar:read` scope.",
    responses={"200": EventListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:read"])
@require_scope("calendar", "read")
def api_search_events(query: SearchEventsQuery):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    q = query.q
    if not q:
        return api_error("VALIDATION_ERROR", "Query parameter 'q' is required", 400)
    limit = min(query.max_results, 200)
    conn = _get_cache_conn(account_id, dek)
    try:
        rows = db_search_events(conn, q, limit=limit)
        items = [_event_to_dict(r) for r in rows]
        return api_paginated(items)
    finally:
        conn.close()


@bp.post(
    "/calendar/free-busy",
    summary="Check free/busy",
    description="Returns busy time slots within a date range. Optionally filter by specific calendar IDs. Requires `calendar:read` scope.",
    responses={"200": FreeBusyResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:read"])
@require_scope("calendar", "read")
def api_free_busy(body: FreeBusyBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    start = body.start
    end = body.end
    if not start or not end:
        return api_error("VALIDATION_ERROR", "'start' and 'end' are required", 400)
    calendar_ids = body.calendar_ids
    conn = _get_cache_conn(account_id, dek)
    try:
        rows = get_conflicting_events(conn, start, end, calendar_ids=calendar_ids)
        busy = []
        for r in rows:
            evt = _event_to_dict(r)
            entry = {"start": evt["start"], "end": evt["end"], "summary": evt["summary"]}
            if "calendar_id" in evt:
                entry["calendar_id"] = evt["calendar_id"]
            busy.append(entry)
        return api_response(busy)
    finally:
        conn.close()


def _get_caldav_session(account):
    from app.shared.models.core import Domain
    from app.modules.mail.services.secrets import decrypt_with_key
    domain = Domain.query.filter_by(id=account.domain_id).first()
    if not domain or not domain.caldav_host:
        raise ApiError("NOT_CONFIGURED", "CalDAV is not configured for this domain", 400)
    scheme = "https" if domain.caldav_use_tls else "http"
    base_url = f"{scheme}://{domain.caldav_host}:{domain.caldav_port or 5232}"
    dek = g.api_context["dek"]
    password = decrypt_with_key(account.encrypted_secret, dek)
    from app.modules.calendar.services import caldav
    s, calendars = caldav.discover_calendars(base_url, account.username, password)
    return s, calendars, base_url, password


@bp.post(
    "/calendar/calendars",
    summary="Create calendar",
    description="Creates a new calendar via CalDAV and caches it locally. Requires `calendar:write` scope.",
    responses={"201": CalendarListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:write"])
@require_scope("calendar", "write")
def api_create_calendar(body: CreateCalendarBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    name = body.name.strip()
    if not name:
        return api_error("VALIDATION_ERROR", "'name' is required", 400)
    color = body.color
    try:
        s, _, base_url, password = _get_caldav_session(account)
        from app.modules.calendar.services import caldav
        cal_url = caldav.create_calendar(s, base_url, account.username, name, color)
    except ApiError:
        raise
    except Exception as e:
        return api_error("CALDAV_ERROR", str(e), 502)
    cal_uid = _uuid.uuid4().hex
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.calendar.services.cache_db import upsert_calendar
        cal_db_id = upsert_calendar(conn, cal_uid, cal_url, displayname=name, color=color)
        cal_row = get_calendar(conn, cal_db_id)
        result = _calendar_to_dict(cal_row) if cal_row else {"uid": cal_uid, "name": name, "color": color}
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "calendar", "calendar_created", {"account_id": account_id, "uid": cal_uid})
    return api_response(result, 201)


@bp.put(
    "/calendar/calendars/<int:calendar_id>",
    summary="Update calendar",
    description="Updates a calendar's display name and/or color. Changes are synced to CalDAV. Requires `calendar:write` scope.",
    responses={"200": CalendarListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:write"])
@require_scope("calendar", "write")
def api_update_calendar(path: CalendarPath, body: UpdateCalendarBody):
    calendar_id = path.calendar_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        cal = get_calendar(conn, calendar_id)
        if not cal:
            return api_error("NOT_FOUND", "Calendar not found", 404)
        cal = _row_to_dict(cal)
        new_name = body.name
        new_color = body.color
        from app.modules.calendar.services.cache_db import update_calendar as db_update_cal
        db_update_cal(conn, calendar_id, displayname=new_name, color=new_color)
    finally:
        conn.close()
    try:
        s, _, _, _ = _get_caldav_session(account)
        from app.modules.calendar.services import caldav
        caldav.update_calendar_props(s, cal.get("href", ""), displayname=new_name, color=new_color)
    except ApiError:
        raise
    except Exception:
        pass
    push_ui_event(g.api_context["customer_id"], "calendar", "calendar_updated", {"account_id": account_id, "calendar_id": calendar_id})
    conn = _get_cache_conn(account_id, dek)
    try:
        cal_row = get_calendar(conn, calendar_id)
        result = _calendar_to_dict(cal_row) if cal_row else {"id": calendar_id}
    finally:
        conn.close()
    return api_response(result)


@bp.post(
    "/calendar/events",
    summary="Create event",
    description="Creates a new calendar event via CalDAV and caches it locally. Supports attendees, reminders, and recurrence rules. Requires `calendar:write` scope.",
    responses={"201": EventDetailResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:write"])
@require_scope("calendar", "write")
def api_create_event(body: CreateEventBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    summary = body.summary
    if not summary:
        return api_error("VALIDATION_ERROR", "'summary' is required", 400)
    calendar_id = body.calendar_id
    if not calendar_id:
        return api_error("VALIDATION_ERROR", "'calendar_id' is required", 400)
    conn = _get_cache_conn(account_id, dek)
    try:
        cal = get_calendar(conn, calendar_id)
        if not cal:
            return api_error("NOT_FOUND", "Calendar not found", 404)
        cal = _row_to_dict(cal)
    finally:
        conn.close()
    event_data = {
        "uid": _uuid.uuid4().hex,
        "summary": summary,
        "description": body.description,
        "location": body.location,
        "dtstart": body.start,
        "dtend": body.end,
        "is_all_day": body.is_all_day,
        "timezone": body.timezone,
        "attendees": body.attendees,
        "alarms": [],
    }
    for r in body.reminders:
        event_data["alarms"].append({"action": r.get("type", "DISPLAY"), "trigger": r.get("trigger_minutes", "-PT15M")})
    if body.recurrence:
        event_data["rrule"] = body.recurrence
    ical_text = generate_icalendar(event_data, uid=event_data["uid"])
    uid = extract_uid(ical_text)
    try:
        s, _, _, _ = _get_caldav_session(account)
        from app.modules.calendar.services import caldav
        href, etag = caldav.create_event(s, cal["href"], ical_text, uid=uid)
    except ApiError:
        raise
    except Exception as e:
        return api_error("CALDAV_ERROR", str(e), 502)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.calendar.services.cache_db import upsert_event
        upsert_event(conn, uid, href, etag, calendar_id, ical_text)
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "calendar", "event_created", {"account_id": account_id, "uid": uid})
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.calendar.services.cache_db import get_event_by_uid
        evt_row = get_event_by_uid(conn, uid, calendar_id=calendar_id)
        result = _event_to_dict(evt_row) if evt_row else {"uid": uid, "summary": summary}
    finally:
        conn.close()
    return api_response(result, 201)


@bp.put(
    "/calendar/events/<int:event_id>",
    summary="Update event",
    description="Updates an existing event by merging provided fields with existing iCalendar data. Only non-null fields are updated. Changes are synced via CalDAV. Requires `calendar:write` scope.",
    responses={"200": EventDetailResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:write"])
@require_scope("calendar", "write")
def api_update_event(path: EventPath, body: UpdateEventBody):
    event_id = path.event_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_event(conn, event_id)
        if not row:
            return api_error("NOT_FOUND", "Event not found", 404)
        d = _row_to_dict(row)
    finally:
        conn.close()
    parsed = {}
    ical_raw = d.get("raw_ical") or d.get("ical_text")
    if ical_raw:
        parsed = parse_icalendar(ical_raw) or {}
    event_data = {
        "uid": d.get("uid"),
        "summary": body.summary if body.summary is not None else parsed.get("summary", ""),
        "description": body.description if body.description is not None else parsed.get("description", ""),
        "location": body.location if body.location is not None else parsed.get("location", ""),
        "dtstart": body.start if body.start is not None else parsed.get("dtstart"),
        "dtend": body.end if body.end is not None else parsed.get("dtend"),
        "is_all_day": body.is_all_day if body.is_all_day is not None else parsed.get("is_all_day", False),
        "timezone": body.timezone if body.timezone is not None else parsed.get("timezone"),
        "attendees": body.attendees if body.attendees is not None else parsed.get("attendees", []),
        "sequence": parsed.get("sequence", 0) + 1,
        "alarms": parsed.get("alarms", []),
    }
    ical_text = generate_icalendar(event_data, uid=event_data["uid"])
    calendar_id = body.calendar_id if body.calendar_id is not None else d.get("calendar_id")
    try:
        s, _, _, _ = _get_caldav_session(account)
        from app.modules.calendar.services import caldav
        href = d.get("href")
        if href:
            caldav.update_event(s, href, ical_text, d.get("etag"))
        else:
            conn2 = _get_cache_conn(account_id, dek)
            cal = get_calendar(conn2, calendar_id)
            cal = _row_to_dict(cal)
            conn2.close()
            href, etag = caldav.create_event(s, cal["href"], ical_text, uid=event_data["uid"])
    except ApiError:
        raise
    except Exception as e:
        return api_error("CALDAV_ERROR", str(e), 502)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.calendar.services.cache_db import upsert_event
        upsert_event(conn, event_data["uid"], href or d.get("href"), d.get("etag"), calendar_id, ical_text)
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "calendar", "event_updated", {"account_id": account_id, "uid": event_data["uid"]})
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.calendar.services.cache_db import get_event_by_uid
        evt_row = get_event_by_uid(conn, event_data["uid"], calendar_id=calendar_id)
        result = _event_to_dict(evt_row) if evt_row else {"uid": event_data["uid"], "summary": event_data["summary"]}
    finally:
        conn.close()
    return api_response(result)


@bp.delete(
    "/calendar/events/<int:event_id>",
    summary="Delete event",
    description="Deletes an event from both the CalDAV server and the local cache. Requires `calendar:write` scope.",
    responses={"200": EventDetailResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["calendar:write"])
@require_scope("calendar", "write")
def api_delete_event(path: EventPath):
    event_id = path.event_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_event(conn, event_id)
        if not row:
            return api_error("NOT_FOUND", "Event not found", 404)
        d = _row_to_dict(row)
        uid = d.get("uid")
        cal_id = d.get("calendar_id")
    finally:
        conn.close()
    try:
        s, _, _, _ = _get_caldav_session(account)
        from app.modules.calendar.services import caldav
        if d.get("href"):
            caldav.delete_event(s, d["href"], d.get("etag"))
    except ApiError:
        raise
    except Exception:
        pass
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.calendar.services.cache_db import delete_event_by_uid
        delete_event_by_uid(conn, uid, calendar_id=cal_id)
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "calendar", "event_deleted", {"account_id": account_id, "uid": uid})
    return api_response(None, 204)
