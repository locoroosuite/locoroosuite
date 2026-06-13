import logging
import json
from datetime import datetime, timezone as dt_timezone

from flask import render_template, redirect, url_for, session, request, jsonify

from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.models.core import CustomerSettings, Domain
from app.shared.timezone import COMMON_TIMEZONES, resolve_user_timezone
from app.modules.calendar.controllers.helpers import (
    calendar_bp,
    _get_account,
    _get_caldav_config,
    _get_credentials,
    _open_cache_for_account,
    _caldav_base_url,
)
from app.modules.calendar.services import caldav, cache_db

logger = logging.getLogger(__name__)

TIME_OPTIONS = []
for _h in range(24):
    for _m in (0, 15, 30, 45):
        TIME_OPTIONS.append(f"{_h:02d}:{_m:02d}")


def _get_user_timezone(user_id):
    settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
    if settings:
        return resolve_user_timezone(settings.timezone)
    return resolve_user_timezone("browser")


def _split_datetime(val, event_tz=None):
    if not val:
        return "", "09:00"
    if len(val) == 10:
        return val, "09:00"
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is not None and event_tz:
            try:
                from zoneinfo import ZoneInfo
                dt = dt.astimezone(ZoneInfo(event_tz))
            except Exception:
                pass
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return val[:10] if len(val) >= 10 else val, "09:00"


def _attendees_json_for_template(form_or_event, field_name="attendees"):
    raw = form_or_event.get(field_name, "") if isinstance(form_or_event, dict) else ""
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return "[]"
        try:
            json.loads(raw)
            return raw
        except (ValueError, TypeError):
            return "[]"
    if isinstance(raw, list):
        return json.dumps(raw)
    return "[]"


def _format_event_time(dt_str, user_tz_name, event_tz=None):
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return dt_str
    if dt.tzinfo is None:
        if event_tz:
            try:
                from zoneinfo import ZoneInfo
                dt = dt.replace(tzinfo=ZoneInfo(event_tz))
            except Exception:
                dt = dt.replace(tzinfo=dt_timezone.utc)
        else:
            dt = dt.replace(tzinfo=dt_timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        target_tz = ZoneInfo(user_tz_name)
    except Exception:
        target_tz = dt_timezone.utc
    local_dt = dt.astimezone(target_tz)
    return local_dt.strftime("%a, %b %d, %Y at %I:%M %p")


def _format_event_date_range(event, user_tz_name):
    if event.get("all_day"):
        start = (event.get("dtstart") or "")[:10]
        end_raw = (event.get("dtend") or "")[:10]
        if end_raw and end_raw != start:
            return f"{start} – {end_raw}"
        return start
    start_str = _format_event_time(event.get("dtstart"), user_tz_name, event.get("timezone"))
    end_str = _format_event_time(event.get("dtend"), user_tz_name, event.get("timezone"))
    if end_str:
        if start_str and end_str:
            start_date = start_str.split(" at ")[0]
            end_date = end_str.split(" at ")[0]
            if start_date == end_date:
                start_time = start_str.split(" at ")[1]
                end_time = end_str.split(" at ")[1]
                return f"{start_date} at {start_time} – {end_time}"
        return f"{start_str} – {end_str}"
    return start_str


@calendar_bp.route("/calendar/events/new", methods=["GET", "POST"])
@require_customer
def event_new():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        return redirect(url_for("calendar.index"))

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        calendars = cache_db.get_all_calendars(conn)
        if not calendars:
            return render_template(
                "event_form.html",
                event=None,
                calendars=[],
                account=account,
                errors={"_server": "No calendars available. Please sync or create a calendar first."},
                dtstart_date="",
                dtstart_time="09:00",
                dtend_date="",
                dtend_time="10:00",
                default_calendar_id="",
                attendees_json="[]",
                time_options=TIME_OPTIONS,
                timezone_options=COMMON_TIMEZONES,
                selected_timezone=_get_user_timezone(user_id),
            )

        if request.method == "GET":
            dtstart_raw = request.args.get("dtstart", "")
            dtend_raw = request.args.get("dtend", "")
            dtstart_date, dtstart_time = _split_datetime(dtstart_raw)
            dtend_date, dtend_time = _split_datetime(dtend_raw)
            cal_id = request.args.get("calendar_id", "")
            prefill_summary = request.args.get("summary", "")
            prefill_description = request.args.get("description", "")
            prefill_attendee = request.args.get("attendee", "")
            attendees_prefill = []
            if prefill_attendee:
                attendees_prefill.append({
                    "email": prefill_attendee,
                    "cn": prefill_attendee,
                    "role": "REQ-PARTICIPANT",
                    "partstat": "NEEDS-ACTION",
                    "rsvp": "TRUE",
                })
            event_prefill = None
            if prefill_summary or prefill_description:
                event_prefill = {"summary": prefill_summary, "description": prefill_description}
            return render_template(
                "event_form.html",
                event=event_prefill,
                calendars=calendars,
                account=account,
                errors={},
                dtstart_date=dtstart_date,
                dtstart_time=dtstart_time,
                dtend_date=dtend_date,
                dtend_time=dtend_time,
                default_calendar_id=cal_id,
                attendees_json=json.dumps(attendees_prefill),
                time_options=TIME_OPTIONS,
                timezone_options=COMMON_TIMEZONES,
                selected_timezone=_get_user_timezone(user_id),
            )

        errors = _validate_event_form(request.form)
        if errors:
            form_dict = request.form.to_dict()
            return render_template(
                "event_form.html",
                event=form_dict,
                calendars=calendars,
                account=account,
                errors=errors,
                dtstart_date=form_dict.get("dtstart_date", ""),
                dtstart_time=form_dict.get("dtstart_time", "09:00"),
                dtend_date=form_dict.get("dtend_date", ""),
                dtend_time=form_dict.get("dtend_time", "10:00"),
                default_calendar_id="",
                attendees_json=_attendees_json_for_template(form_dict),
                time_options=TIME_OPTIONS,
                timezone_options=COMMON_TIMEZONES,
                selected_timezone=form_dict.get("timezone", _get_user_timezone(user_id)),
            )

        data = _form_to_event_data(request.form, user_id=user_id)
        if data.get("attendees") and not data.get("organizer"):
            data["organizer"] = {"cn": account.email_address, "email": account.email_address}
        from app.modules.calendar.services.icalendar import generate_icalendar
        ical_text = generate_icalendar(data)

        password = _get_credentials(account)
        if not password:
            return redirect(url_for("mail.login"))

        cal_id = int(request.form.get("calendar_id"))
        cal = cache_db.get_calendar(conn, cal_id)
        if not cal:
            return redirect(url_for("calendar.index"))

        try:
            s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
            href, etag = caldav.create_event(s, cal["href"], ical_text, uid=data.get("uid"))
            from app.modules.calendar.services.icalendar import extract_uid
            uid = extract_uid(ical_text)
            cache_db.upsert_event(conn, uid, href, etag, cal_id, ical_text)
        except Exception:
            logger.exception("failed to create event on CalDAV")
            form_dict = request.form.to_dict()
            return render_template(
                "event_form.html",
                event=form_dict,
                calendars=calendars,
                account=account,
                errors={"_server": "Failed to save event. Please check your connection and retry."},
                dtstart_date=form_dict.get("dtstart_date", ""),
                dtstart_time=form_dict.get("dtstart_time", "09:00"),
                dtend_date=form_dict.get("dtend_date", ""),
                dtend_time=form_dict.get("dtend_time", "10:00"),
                default_calendar_id="",
                attendees_json=_attendees_json_for_template(form_dict),
                time_options=TIME_OPTIONS,
                timezone_options=COMMON_TIMEZONES,
                selected_timezone=form_dict.get("timezone", _get_user_timezone(user_id)),
            )

        if data.get("attendees"):
            return redirect(url_for("calendar.event_detail", event_id=cache_db.get_event_by_uid(conn, uid)["id"], send_updates="1"))
        return redirect(url_for("calendar.index"))
    finally:
        conn.close()


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
        return render_template("event_detail.html", event=event, account=account, source_email=source_email, user_timezone=user_tz_name, event_timezone=event_tz, is_invitee=is_invitee, my_rsvp_status=my_rsvp_status)
    finally:
        conn.close()


@calendar_bp.route("/calendar/events/<int:event_id>/edit", methods=["GET", "POST"])
@require_customer
def event_edit(event_id):
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

        calendars = cache_db.get_all_calendars(conn)

        if request.method == "GET":
            attendees_list = []
            if isinstance(event.get("attendees"), str):
                try:
                    attendees_list = json.loads(event["attendees"])
                except (ValueError, TypeError):
                    attendees_list = []
            else:
                attendees_list = event.get("attendees") or []
            event["attendees_list"] = attendees_list
            event["location"] = event.get("location") or ""
            event["description"] = event.get("description") or ""
            event_tz = event.get("timezone", "") or ""
            dtstart_date, dtstart_time = _split_datetime(event.get("dtstart", ""), event_tz)
            dtend_date, dtend_time = _split_datetime(event.get("dtend", ""), event_tz)
            return render_template(
                "event_form.html",
                event=event,
                calendars=calendars,
                account=account,
                errors={},
                dtstart_date=dtstart_date,
                dtstart_time=dtstart_time,
                dtend_date=dtend_date,
                dtend_time=dtend_time,
                default_calendar_id="",
                attendees_json=json.dumps(attendees_list),
                time_options=TIME_OPTIONS,
                timezone_options=COMMON_TIMEZONES,
                selected_timezone=event_tz or _get_user_timezone(user_id),
            )

        errors = _validate_event_form(request.form)
        if errors:
            form_dict = request.form.to_dict()
            event.update(form_dict)
            return render_template(
                "event_form.html",
                event=event,
                calendars=calendars,
                account=account,
                errors=errors,
                dtstart_date=form_dict.get("dtstart_date", ""),
                dtstart_time=form_dict.get("dtstart_time", "09:00"),
                dtend_date=form_dict.get("dtend_date", ""),
                dtend_time=form_dict.get("dtend_time", "10:00"),
                default_calendar_id="",
                attendees_json=_attendees_json_for_template(form_dict),
                time_options=TIME_OPTIONS,
                timezone_options=COMMON_TIMEZONES,
                selected_timezone=form_dict.get("timezone", _get_user_timezone(user_id)),
            )

        data = _form_to_event_data(request.form, user_id=user_id)
        data["uid"] = event["uid"]
        data["sequence"] = event.get("sequence", 0) + 1
        if isinstance(event.get("organizer"), str):
            try:
                data["organizer"] = json.loads(event["organizer"])
            except (ValueError, TypeError):
                pass
        if data.get("attendees") and not data.get("organizer"):
            data["organizer"] = {"cn": account.email_address, "email": account.email_address}

        from app.modules.calendar.services.icalendar import generate_icalendar
        ical_text = generate_icalendar(data, uid=event["uid"])

        config = _get_caldav_config(account)
        password = _get_credentials(account)
        if not config or not password:
            return redirect(url_for("mail.login"))

        cal_id = int(request.form.get("calendar_id", event["calendar_id"]))
        try:
            s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
            if event.get("href"):
                etag = caldav.update_event(s, event["href"], ical_text, event.get("etag"))
            else:
                cal = cache_db.get_calendar(conn, cal_id)
                href, etag = caldav.create_event(s, cal["href"], ical_text, uid=event["uid"])
                event["href"] = href
            cache_db.upsert_event(conn, event["uid"], event.get("href", ""), etag, cal_id, ical_text)
        except Exception:
            logger.exception("failed to update event on CalDAV")
            form_dict = request.form.to_dict()
            event.update(form_dict)
            return render_template(
                "event_form.html",
                event=event,
                calendars=calendars,
                account=account,
                errors={"_server": "Failed to save event. Please check your connection and retry."},
                dtstart_date=form_dict.get("dtstart_date", ""),
                dtstart_time=form_dict.get("dtstart_time", "09:00"),
                dtend_date=form_dict.get("dtend_date", ""),
                dtend_time=form_dict.get("dtend_time", "10:00"),
                default_calendar_id="",
                attendees_json=_attendees_json_for_template(form_dict),
                time_options=TIME_OPTIONS,
                timezone_options=COMMON_TIMEZONES,
                selected_timezone=form_dict.get("timezone", _get_user_timezone(user_id)),
            )

        if data.get("attendees"):
            return redirect(url_for("calendar.event_detail", event_id=event_id, send_updates="1"))
        return redirect(url_for("calendar.event_detail", event_id=event_id))
    finally:
        conn.close()


@calendar_bp.route("/calendar/events/<int:event_id>/delete", methods=["POST"])
@require_customer
def event_delete(event_id):
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

        attendees = []
        raw_attendees = event.get("attendees")
        if isinstance(raw_attendees, str):
            try:
                attendees = json.loads(raw_attendees)
            except (ValueError, TypeError):
                attendees = []
        elif isinstance(raw_attendees, list):
            attendees = raw_attendees

        send_notification = request.form.get("send_notification") == "1"
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
                    send_imip_email(domain, account, event_data, "CANCEL", attendees, uid=event.get("uid"))
            except Exception:
                logger.exception("failed to send cancellation imip event_id=%s", event_id)

        config = _get_caldav_config(account)
        password = _get_credentials(account)
        if config and password and event.get("href"):
            try:
                s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
                caldav.delete_event(s, event["href"], event.get("etag"))
            except Exception:
                logger.exception("failed to delete event from CalDAV")

        cache_db.delete_event_by_uid(conn, event["uid"], event.get("calendar_id"))
    finally:
        conn.close()

    return redirect(url_for("calendar.index"))


def _validate_event_form(form):
    errors = {}
    summary = form.get("summary", "").strip()
    dtstart_date = form.get("dtstart_date", "").strip()
    calendar_id = form.get("calendar_id", "").strip()
    if not summary:
        errors["summary"] = "Event title is required."
    if not dtstart_date:
        errors["dtstart"] = "Start date is required."
    if not calendar_id:
        errors["calendar_id"] = "Calendar is required."
    return errors


def _form_to_event_data(form, user_id=None):
    dtstart_date = form.get("dtstart_date", "").strip()
    dtstart_time = form.get("dtstart_time", "09:00").strip()
    dtend_date = form.get("dtend_date", "").strip()
    dtend_time = form.get("dtend_time", "10:00").strip()
    all_day = form.get("all_day") == "1"
    tz = form.get("timezone", "").strip()
    if not tz and user_id:
        tz = _get_user_timezone(user_id)

    if all_day and dtstart_date:
        dtstart = dtstart_date
    elif dtstart_date:
        dtstart = f"{dtstart_date}T{dtstart_time}"
    else:
        dtstart = None

    if all_day and dtend_date:
        from datetime import date as date_cls, timedelta as timedelta_cls
        try:
            end_d = date_cls.fromisoformat(dtend_date) + timedelta_cls(days=1)
            dtend = end_d.isoformat()
        except ValueError:
            dtend = dtend_date
    elif dtend_date:
        dtend = f"{dtend_date}T{dtend_time}"
    else:
        dtend = None

    reminders = []
    reminder_trigger = form.get("reminder_trigger", "").strip()
    if reminder_trigger:
        reminders.append({
            "trigger": reminder_trigger,
            "action": "DISPLAY",
            "description": form.get("summary", "").strip(),
        })

    attendees = []
    raw = form.get("attendees", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for att in parsed:
                    email = att.get("email", "").strip() if isinstance(att, dict) else str(att).strip()
                    if not email:
                        continue
                    attendees.append({
                        "email": email,
                        "cn": att.get("cn", email) if isinstance(att, dict) else email,
                        "role": att.get("role", "REQ-PARTICIPANT") if isinstance(att, dict) else "REQ-PARTICIPANT",
                        "partstat": att.get("partstat", "NEEDS-ACTION") if isinstance(att, dict) else "NEEDS-ACTION",
                        "rsvp": att.get("rsvp", "TRUE") if isinstance(att, dict) else "TRUE",
                    })
        except (ValueError, TypeError):
            pass

    return {
        "summary": form.get("summary", "").strip(),
        "description": form.get("description", "").strip() or None,
        "location": form.get("location", "").strip() or None,
        "dtstart": dtstart,
        "dtend": dtend,
        "all_day": all_day,
        "timezone": tz or None,
        "rrule": form.get("rrule", "").strip() or None,
        "status": form.get("status", "CONFIRMED"),
        "class_": form.get("class_", "PUBLIC"),
        "alarms": reminders,
        "attendees": attendees,
    }
