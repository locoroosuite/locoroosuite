"""JSON event CRUD API for the calendar dialog/bottom-sheet editor (U12.56b).

The legacy full-page form (event_form.html) POSTed HTML redirects; the
Google-style editor needs JSON endpoints. Validation, CalDAV persistence and
iMIP flows are shared with the previous controller paths.
"""

import contextlib
import json
import logging

from flask import jsonify, request, session
from flask_babel import _

from app.modules.calendar.controllers.helpers import (
    _caldav_base_url,
    _get_account,
    _get_caldav_config,
    _get_credentials,
    _open_cache_for_account,
    calendar_bp,
    parse_negative_duration,
)
from app.modules.calendar.services import cache_db, caldav
from app.shared.auth import require_customer

logger = logging.getLogger(__name__)

RRULE_PRESETS = (
    "",
    "FREQ=DAILY",
    "FREQ=WEEKLY",
    "FREQ=WEEKLY;INTERVAL=2",
    "FREQ=MONTHLY",
    "FREQ=YEARLY",
)
STATUS_OPTIONS = ("CONFIRMED", "TENTATIVE", "CANCELLED")
CLASS_OPTIONS = ("PUBLIC", "PRIVATE", "CONFIDENTIAL")
REMINDER_ACTIONS = ("DISPLAY", "EMAIL")
MAX_REMINDERS = 10


def _get_user_timezone(user_id):
    from app.modules.calendar.controllers.events import _get_user_timezone as _impl

    return _impl(user_id)


def _parse_payload_datetime(payload, prefix):
    """Combine "<prefix>_date"/"<prefix>_time" or an explicit "<prefix>" value."""
    date_val = (payload.get(f"{prefix}_date") or "").strip()
    time_val = (payload.get(f"{prefix}_time") or "").strip()
    if date_val:
        return f"{date_val}T{time_val or '09:00'}"
    explicit = (payload.get(prefix) or "").strip()
    return explicit or None


def _validate_payload(payload):
    errors = {}
    if not (payload.get("summary") or "").strip():
        errors["summary"] = _("Event title is required.")
    if not _parse_payload_datetime(payload, "dtstart"):
        errors["dtstart"] = _("Start date is required.")
    if not payload.get("calendar_id"):
        errors["calendar_id"] = _("Calendar is required.")
    _alarms, reminder_error = _normalize_reminders(payload.get("reminders"), "")
    if reminder_error:
        errors["reminders"] = reminder_error
    return errors


def _normalize_attendees(raw):
    attendees = []
    if isinstance(raw, str):
        with contextlib.suppress(ValueError, TypeError):
            raw = json.loads(raw)
    if not isinstance(raw, list):
        return attendees
    for att in raw:
        if isinstance(att, str):
            att = {"email": att}
        if not isinstance(att, dict):
            continue
        email = (att.get("email") or "").strip()
        if not email:
            continue
        attendees.append(
            {
                "email": email,
                "cn": (att.get("cn") or email).strip() or email,
                "role": att.get("role") or "REQ-PARTICIPANT",
                "partstat": att.get("partstat") or "NEEDS-ACTION",
                "rsvp": att.get("rsvp") if att.get("rsvp") else "TRUE",
            }
        )
    return attendees


def _normalize_reminders(raw, summary):
    """Validate and normalize a reminders payload into VALARM dicts.

    Accepts a list of ``{"action": "DISPLAY"|"EMAIL", "trigger": "-PT15M"}``
    dicts (the legacy ``reminder_trigger`` scalar is intentionally dropped:
    callers that send it simply get no alarms). Returns ``(alarms, error)``
    where exactly one of the two is None/empty on success.
    """
    if raw is None or raw == "":
        return [], None
    if isinstance(raw, str):
        with contextlib.suppress(ValueError, TypeError):
            raw = json.loads(raw)
    if not isinstance(raw, list):
        return None, _("Invalid notifications.")
    if len(raw) > MAX_REMINDERS:
        return None, _("Too many notifications (maximum %(max)d).", max=MAX_REMINDERS)
    alarms = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            return None, _("Invalid notifications.")
        action = str(item.get("action") or item.get("type") or "DISPLAY").strip().upper()
        if action not in REMINDER_ACTIONS:
            return None, _("Invalid notification type.")
        trigger = str(item.get("trigger") or "").strip()
        if parse_negative_duration(trigger) is None:
            return None, _("Invalid notification time.")
        if (action, trigger) in seen:
            return None, _("Duplicate notification.")
        seen.add((action, trigger))
        alarms.append(
            {
                "trigger": trigger,
                "action": action,
                "description": (summary or "").strip(),
            }
        )
    return alarms, None


def _payload_to_event_data(payload, user_id):
    """Build a generate_icalendar-compatible dict from a JSON payload."""
    all_day = bool(payload.get("all_day"))
    tz = (payload.get("timezone") or "").strip()
    if not tz and not all_day:
        tz = _get_user_timezone(user_id)

    dtstart = _parse_payload_datetime(payload, "dtstart")
    dtend = _parse_payload_datetime(payload, "dtend")
    if dtstart and all_day:
        dtstart = dtstart[:10]
    if dtend and all_day:
        from datetime import date as date_cls
        from datetime import timedelta

        try:
            end_d = date_cls.fromisoformat(dtend[:10]) + timedelta(days=1)
            dtend = end_d.isoformat()
        except ValueError:
            dtend = dtend[:10]

    reminders, reminder_error = _normalize_reminders(
        payload.get("reminders"), (payload.get("summary") or "").strip()
    )
    if reminder_error:
        reminders = []

    rrule = (payload.get("rrule") or "").strip()
    if rrule and rrule not in RRULE_PRESETS:
        logger.warning("rejecting non-preset rrule %r", rrule)
        rrule = ""
    status = payload.get("status") if payload.get("status") in STATUS_OPTIONS else "CONFIRMED"
    class_ = payload.get("class_") if payload.get("class_") in CLASS_OPTIONS else "PUBLIC"

    return {
        "summary": (payload.get("summary") or "").strip(),
        "description": (payload.get("description") or "").strip() or None,
        "location": (payload.get("location") or "").strip() or None,
        "dtstart": dtstart,
        "dtend": dtend,
        "all_day": all_day,
        "timezone": tz or None,
        "rrule": rrule or None,
        "status": status,
        "class_": class_,
        "alarms": reminders,
        "attendees": _normalize_attendees(payload.get("attendees")),
    }


def _load_event_or_none(conn, event_id):
    event = cache_db.get_event(conn, event_id)
    if not event:
        return None
    if isinstance(event.get("attendees"), str):
        with contextlib.suppress(ValueError, TypeError):
            event["attendees"] = json.loads(event["attendees"])
    if isinstance(event.get("organizer"), str):
        with contextlib.suppress(ValueError, TypeError):
            event["organizer"] = json.loads(event["organizer"])
    return event


def _event_ical_base(event):
    """Parse an event's stored iCalendar into a generate_icalendar dict."""
    from app.shared.icalendar import parse_icalendar

    parsed = parse_icalendar(event.get("raw_ical") or "")
    if isinstance(parsed.get("attendees"), list):
        parsed["attendees"] = [
            {**att, "rsvp": "TRUE" if att.get("rsvp") else "FALSE"} for att in parsed["attendees"]
        ]
    return parsed


def _serialize_single(conn, event_id):
    event = cache_db.get_event(conn, event_id)
    if not event:
        return None
    events = _serialize_with_calendar(conn, [dict(event)])
    return events[0] if events else None


def _serialize_with_calendar(conn, events):
    from app.modules.calendar.controllers.views import _serialize_events

    for e in events:
        cal = cache_db.get_calendar(conn, e.get("calendar_id"))
        e["calendar_color"] = (cal or {}).get("color", "#4285f4")
        e["calendar_name"] = (cal or {}).get("displayname", "")
    return _serialize_events(events)


def _attendee_count(event):
    attendees = event.get("attendees")
    return len(attendees) if isinstance(attendees, list) else 0


@calendar_bp.route("/calendar/api/events/<int:event_id>", methods=["GET"])
@require_customer
def api_event_get(event_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"error": _("No active account.")}), 400
    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": _("Cache unavailable.")}), 400
    try:
        serialized = _serialize_single(conn, event_id)
        if serialized is None:
            return jsonify({"error": _("Event not found.")}), 404
        return jsonify(serialized)
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/events", methods=["POST"])
@require_customer
def api_event_create():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"ok": False, "error": _("No active account.")}), 400

    payload = request.get_json(silent=True) or {}
    errors = _validate_payload(payload)
    if errors:
        first_error = (
            errors.get("summary")
            or errors.get("dtstart")
            or errors.get("calendar_id")
            or errors.get("reminders")
        )
        return jsonify({"ok": False, "error": first_error or _("Invalid event.")}), 400

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        return jsonify({"ok": False, "error": _("CalDAV not configured.")}), 400

    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"ok": False, "error": _("Cache unavailable.")}), 400

    try:
        cal_id = int(payload.get("calendar_id") or 0)
        cal = cache_db.get_calendar(conn, cal_id)
        if not cal:
            return jsonify({"ok": False, "error": _("Calendar not found.")}), 404

        data = _payload_to_event_data(payload, user_id)
        if data.get("attendees") and not data.get("organizer"):
            data["organizer"] = {"cn": account.email_address, "email": account.email_address}

        from app.shared.icalendar import generate_icalendar

        ical_text = generate_icalendar(data)

        password = _get_credentials(account)
        if not password:
            return jsonify({"ok": False, "error": _("Credentials unavailable.")}), 401

        try:
            s, _unused = caldav.discover_calendars(
                _caldav_base_url(config), account.username, password
            )
            href, etag = caldav.create_event(s, cal["href"], ical_text, uid=data.get("uid"))
            from app.shared.icalendar import extract_uid

            uid = extract_uid(ical_text)
            event_id = cache_db.upsert_event(conn, uid, href, etag, cal_id, ical_text)
        except Exception:
            logger.exception("failed to create event via JSON api")
            return jsonify(
                {
                    "ok": False,
                    "error": _("Failed to save event. Please check your connection and retry."),
                }
            ), 502

        return jsonify(
            {"ok": True, "event_id": event_id, "notify_guests": bool(data.get("attendees"))}
        )
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/events/<int:event_id>", methods=["PUT"])
@require_customer
def api_event_update(event_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"ok": False, "error": _("No active account.")}), 400

    payload = request.get_json(silent=True) or {}
    errors = _validate_payload(payload)
    if errors:
        first_error = (
            errors.get("summary")
            or errors.get("dtstart")
            or errors.get("calendar_id")
            or errors.get("reminders")
        )
        return jsonify({"ok": False, "error": first_error or _("Invalid event.")}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"ok": False, "error": _("Cache unavailable.")}), 400

    try:
        event = _load_event_or_none(conn, event_id)
        if not event:
            return jsonify({"ok": False, "error": _("Event not found.")}), 404

        base = _event_ical_base(event)
        overlay = _payload_to_event_data(payload, user_id)
        for key in (
            "summary",
            "description",
            "location",
            "dtstart",
            "dtend",
            "all_day",
            "timezone",
            "rrule",
            "status",
            "class_",
            "alarms",
            "attendees",
        ):
            if key in overlay:
                base[key] = overlay[key]
        if base.get("attendees") and not base.get("organizer"):
            base["organizer"] = {"cn": account.email_address, "email": account.email_address}
        base["uid"] = event["uid"]
        base["sequence"] = int(event.get("sequence") or 0) + 1

        from app.shared.icalendar import generate_icalendar

        ical_text = generate_icalendar(base, uid=event["uid"])

        config = _get_caldav_config(account)
        password = _get_credentials(account)
        if not config or not password:
            return jsonify({"ok": False, "error": _("Credentials unavailable.")}), 401

        cal_id = int(payload.get("calendar_id") or event["calendar_id"])
        try:
            s, _unused = caldav.discover_calendars(
                _caldav_base_url(config), account.username, password
            )
            new_cal = cache_db.get_calendar(conn, cal_id)
            if not new_cal:
                return jsonify({"ok": False, "error": _("Calendar not found.")}), 404
            calendar_changed = cal_id != event["calendar_id"]
            if calendar_changed:
                href, etag = caldav.create_event(s, new_cal["href"], ical_text, uid=event["uid"])
                if event.get("href"):
                    with contextlib.suppress(Exception):
                        caldav.delete_event(s, event["href"], event.get("etag"))
            elif event.get("href"):
                etag = caldav.update_event(s, event["href"], ical_text, event.get("etag"))
                href = event["href"]
            else:
                href, etag = caldav.create_event(s, new_cal["href"], ical_text, uid=event["uid"])
            cache_db.upsert_event(conn, event["uid"], href, etag, cal_id, ical_text)
        except Exception:
            logger.exception("failed to update event via JSON api event_id=%s", event_id)
            return jsonify(
                {
                    "ok": False,
                    "error": _("Failed to save event. Please check your connection and retry."),
                }
            ), 502

        return jsonify(
            {"ok": True, "event_id": event_id, "notify_guests": bool(base.get("attendees"))}
        )
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/events/<int:event_id>/move", methods=["POST"])
@require_customer
def api_event_move(event_id):
    """Time-only update for drag-move / drag-resize (U12.56e, U12.20)."""
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"ok": False, "error": _("No active account.")}), 400

    payload = request.get_json(silent=True) or {}
    dtstart = (payload.get("dtstart") or "").strip()
    dtend = (payload.get("dtend") or "").strip()
    if not dtstart:
        return jsonify({"ok": False, "error": _("Start time is required.")}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"ok": False, "error": _("Cache unavailable.")}), 400

    try:
        event = _load_event_or_none(conn, event_id)
        if not event:
            return jsonify({"ok": False, "error": _("Event not found.")}), 404

        base = _event_ical_base(event)
        all_day = bool(event.get("all_day"))
        base["all_day"] = all_day
        base["dtstart"] = dtstart[:10] if all_day else dtstart
        base["dtend"] = (dtend[:10] if all_day else dtend) if dtend else None
        base["uid"] = event["uid"]
        if _attendee_count(event):
            base["sequence"] = int(event.get("sequence") or 0) + 1

        from app.shared.icalendar import generate_icalendar

        ical_text = generate_icalendar(base, uid=event["uid"])

        config = _get_caldav_config(account)
        password = _get_credentials(account)
        if not config or not password:
            return jsonify({"ok": False, "error": _("Credentials unavailable.")}), 401

        try:
            s, _unused = caldav.discover_calendars(
                _caldav_base_url(config), account.username, password
            )
            if event.get("href"):
                etag = caldav.update_event(s, event["href"], ical_text, event.get("etag"))
                href = event["href"]
            else:
                cal = cache_db.get_calendar(conn, event["calendar_id"])
                if not cal:
                    return jsonify({"ok": False, "error": _("Calendar not found.")}), 404
                href, etag = caldav.create_event(s, cal["href"], ical_text, uid=event["uid"])
            cache_db.upsert_event(conn, event["uid"], href, etag, event["calendar_id"], ical_text)
        except Exception:
            logger.exception("failed to move event via JSON api event_id=%s", event_id)
            return jsonify(
                {
                    "ok": False,
                    "error": _("Failed to move event. Please check your connection and retry."),
                }
            ), 502

        return jsonify(
            {"ok": True, "event_id": event_id, "notify_guests": _attendee_count(event) > 0}
        )
    finally:
        conn.close()
