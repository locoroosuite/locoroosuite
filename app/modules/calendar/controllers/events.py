import json
import logging
from datetime import UTC, datetime

from flask import jsonify, redirect, render_template, request, session, url_for
from flask_babel import _

from app.modules.calendar.controllers.helpers import (
    _caldav_base_url,
    _get_account,
    _get_caldav_config,
    _get_credentials,
    _humanize_reminder,
    _open_cache_for_account,
    calendar_bp,
)
from app.modules.calendar.services import cache_db, caldav
from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.models.core import CustomerSettings
from app.shared.timezone import resolve_user_timezone

logger = logging.getLogger(__name__)


def _get_user_timezone(user_id):
    settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
    if settings:
        return resolve_user_timezone(settings.timezone)
    return resolve_user_timezone("browser")


def _format_event_time(dt_str, user_tz_name, event_tz=None, vtimezones=None):
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return dt_str
    if dt.tzinfo is None:
        if event_tz:
            from app.shared.tzid_resolver import resolve_tzid

            tz = resolve_tzid(event_tz, vtimezones=vtimezones, dt=dt)
            if tz is not None:
                dt = dt.replace(tzinfo=tz)
            else:
                logger.warning("unresolvable TZID %r on event; assuming UTC", event_tz)
                dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.replace(tzinfo=UTC)
    try:
        from zoneinfo import ZoneInfo

        target_tz = ZoneInfo(user_tz_name)
    except Exception:
        target_tz = UTC
    local_dt = dt.astimezone(target_tz)
    from babel.dates import format_datetime

    from app.shared.i18n import current_locale_name

    locale = current_locale_name()
    return (
        f"{format_datetime(local_dt, 'EEE, MMM dd, y', locale=locale)}"
        f" {_('at')} {format_datetime(local_dt, 'hh:mm a', locale=locale)}"
    )


def _event_vtimezones(event):
    """Extract parsed VTIMEZONE definitions from an event's raw iCalendar."""
    raw_ical = (event or {}).get("raw_ical") or ""
    if not raw_ical:
        return None
    from app.shared.icalendar import parse_icalendar

    parsed = parse_icalendar(raw_ical)
    return parsed.get("vtimezones")


def _format_event_date_range(event, user_tz_name):
    if event.get("all_day"):
        start = (event.get("dtstart") or "")[:10]
        end_raw = (event.get("dtend") or "")[:10]
        if end_raw and end_raw != start:
            return f"{start} \u2013 {end_raw}"
        return start
    vtimezones = _event_vtimezones(event)
    event_tz = event.get("timezone")
    start_str = _format_event_time(event.get("dtstart"), user_tz_name, event_tz, vtimezones)
    end_str = _format_event_time(event.get("dtend"), user_tz_name, event_tz, vtimezones)
    if end_str:
        if start_str and end_str:
            start_date = start_str.split(" at ")[0]
            end_date = end_str.split(" at ")[0]
            if start_date == end_date:
                start_time = start_str.split(" at ")[1]
                end_time = end_str.split(" at ")[1]
                return f"{start_date} at {start_time} \u2013 {end_time}"
        return f"{start_str} \u2013 {end_str}"
    return start_str


@calendar_bp.route("/calendar/events/new")
@require_customer
def event_new():
    """Legacy entry point: redirect to the calendar index with prefill params.

    The event editor is now a dialog/bottom sheet (U12.56b); callers linking
    here (e.g. mail's "Create event" action, U12.39c) keep working and land
    on the index with the dialog pre-populated.
    """
    params = {}
    for key in ("summary", "description", "attendee", "dtstart", "dtend", "calendar_id"):
        value = request.args.get(key, "").strip()
        if value:
            params[key] = value
    params["new"] = "1"
    return redirect(url_for("calendar.index", **params))


@calendar_bp.route("/calendar/events/<int:event_id>")
@require_customer
def event_detail(event_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        event = cache_db.get_event(conn, event_id)
        if not event:
            return redirect(url_for("calendar.index"))
        cal = cache_db.get_calendar(conn, event["calendar_id"])
        event["calendar_color"] = cal.get("color", "#4285f4") if cal else "#4285f4"
        event["calendar_name"] = cal.get("displayname", "") if cal else ""
        if isinstance(event.get("attendees"), str):
            try:
                event["attendees"] = json.loads(event["attendees"])
            except (ValueError, TypeError):
                event["attendees"] = []
        if isinstance(event.get("organizer"), str):
            try:
                event["organizer"] = json.loads(event["organizer"])
            except (ValueError, TypeError):
                event["organizer"] = None
        source_email = None
        if event.get("source_email_message_id") and event.get("source_email_account_id"):
            source_email = {
                "message_id": event["source_email_message_id"],
                "account_id": event["source_email_account_id"],
            }
        user_tz_name = _get_user_timezone(user_id)
        event["when_display"] = _format_event_date_range(event, user_tz_name)
        event["reminder_labels"] = [
            _humanize_reminder(r.get("trigger_val"), r.get("action"))
            for r in (event.get("reminders") or [])
            if isinstance(r, dict)
        ]
        event_tz = event.get("timezone") or ""
        my_rsvp_status = None
        is_invitee = False
        organizer = event.get("organizer")
        attendees = event.get("attendees") or []
        if organizer and attendees:
            org_email = (organizer.get("email") if isinstance(organizer, dict) else "") or ""
            my_email = account.email_address.lower()
            if org_email.lower() != my_email:
                for att in attendees:
                    if isinstance(att, dict) and att.get("email", "").lower() == my_email:
                        is_invitee = True
                        my_rsvp_status = att.get("partstat", "NEEDS-ACTION")
                        break
        return render_template(
            "event_detail.html",
            event=event,
            account=account,
            source_email=source_email,
            user_timezone=user_tz_name,
            event_timezone=event_tz,
            is_invitee=is_invitee,
            my_rsvp_status=my_rsvp_status,
        )
    finally:
        conn.close()


@calendar_bp.route("/calendar/events/<int:event_id>/delete", methods=["POST"])
@require_customer
def event_delete(event_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        event = cache_db.get_event(conn, event_id)
        if not event:
            if wants_json:
                return jsonify({"ok": False, "error": _("Event not found.")}), 404
            return redirect(url_for("calendar.index"))

        attendees = []
        raw_attendees = event.get("attendees")
        if isinstance(raw_attendees, str):
            try:
                attendees = json.loads(raw_attendees)
            except (ValueError, TypeError):
                attendees = []
        elif isinstance(raw_attendees, list):
            attendees = raw_attendees

        payload = request.get_json(silent=True) or {}
        send_notification = request.form.get("send_notification") == "1" or bool(
            payload.get("send_notification")
        )
        if send_notification and attendees:
            try:
                from app.modules.calendar.services.imip import send_imip_email
                from app.shared.models.core import Domain

                domain = db.session.get(Domain, account.domain_id)
                if domain and domain.is_active:
                    event_data = {
                        "summary": event.get("summary", ""),
                        "description": event.get("description"),
                        "location": event.get("location"),
                        "dtstart": event.get("dtstart"),
                        "dtend": event.get("dtend"),
                        "all_day": event.get("all_day"),
                        "timezone": event.get("timezone"),
                        "uid": event.get("uid"),
                        "sequence": event.get("sequence", 0),
                        "status": "CANCELLED",
                        "organizer": {"cn": account.email_address, "email": account.email_address},
                        "attendees": attendees,
                    }
                    send_imip_email(
                        domain, account, event_data, "CANCEL", attendees, uid=event.get("uid")
                    )
            except Exception:
                logger.exception("failed to send cancellation imip event_id=%s", event_id)

        config = _get_caldav_config(account)
        password = _get_credentials(account)
        if config and password and event.get("href"):
            try:
                s, _unused = caldav.discover_calendars(
                    _caldav_base_url(config), account.username, password
                )
                caldav.delete_event(s, event["href"], event.get("etag"))
            except Exception:
                logger.exception("failed to delete event from CalDAV")

        cache_db.delete_event_by_uid(conn, event["uid"], event.get("calendar_id"))
    finally:
        conn.close()

    if wants_json:
        return jsonify({"ok": True})
    return redirect(url_for("calendar.index"))
