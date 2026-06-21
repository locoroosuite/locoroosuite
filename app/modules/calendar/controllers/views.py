import logging
from datetime import datetime, timezone

from flask import render_template, redirect, url_for, session, request, jsonify

from app.shared.auth import require_customer
from app.shared.models.core import CustomerSettings
from app.shared.timezone import resolve_user_timezone
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


@calendar_bp.route("/calendar/")
@require_customer
def index():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        return render_template("index.html", calendars=[], caldav_configured=False, view="week")

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        sync_error = False
        calendars = cache_db.get_all_calendars(conn)
        if not calendars:
            try:
                _sync_calendars_and_events(conn, account, config)
                calendars = cache_db.get_all_calendars(conn)
            except Exception:
                logger.exception("calendar sync failed on index load account_id=%s", account.id)
                sync_error = True
        view = request.args.get("view", "week")
        date_str = request.args.get("date")
        if date_str:
            selected_date = date_str
        else:
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            user_tz_name = resolve_user_timezone(settings.timezone if settings else "browser")
            try:
                from zoneinfo import ZoneInfo
                user_tz = ZoneInfo(user_tz_name)
            except Exception:
                user_tz = timezone.utc
            selected_date = datetime.now(user_tz).strftime("%Y-%m-%d")
        return render_template(
            "index.html",
            calendars=calendars,
            caldav_configured=True,
            sync_error=sync_error,
            view=view,
            selected_date=selected_date,
            account=account,
        )
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/events")
@require_customer
def api_events():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify([])

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        return jsonify([])

    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify([])

    try:
        start = request.args.get("start", "")
        end = request.args.get("end", "")
        cal_ids = request.args.get("calendar_ids", "")
        if not start or not end:
            return jsonify([])

        calendar_ids = None
        if cal_ids:
            try:
                calendar_ids = [int(x) for x in cal_ids.split(",") if x.strip()]
            except ValueError:
                pass

        events = cache_db.get_events_range(conn, start, end, calendar_ids)
        return jsonify(_serialize_events(events))
    except Exception:
        logger.exception("calendar api events failed")
        return jsonify([])
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/upcoming")
@require_customer
def api_upcoming():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify([])

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify([])

    try:
        limit = request.args.get("limit", 30, type=int)
        events = cache_db.get_upcoming_events(conn, limit)
        return jsonify(_serialize_events(events))
    except Exception:
        logger.exception("calendar api upcoming failed")
        return jsonify([])
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/search")
@require_customer
def api_search():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify([])

    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify([])

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify([])

    try:
        results = cache_db.search_events_api(conn, q)
        return jsonify(results)
    except Exception:
        logger.exception("calendar api search failed")
        return jsonify([])
    finally:
        conn.close()


@calendar_bp.route("/calendar/sync", methods=["POST"])
@require_customer
def sync():
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
        _sync_calendars_and_events(conn, account, config)
    except Exception:
        logger.exception("calendar sync failed")
    finally:
        conn.close()

    return redirect(url_for("calendar.index"))


@calendar_bp.route("/calendar/calendars/new", methods=["GET", "POST"])
@require_customer
def new_calendar():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        return redirect(url_for("calendar.index"))

    if request.method == "GET":
        return render_template("calendar_form.html", calendar=None, account=account, errors={})

    name = request.form.get("name", "").strip()
    color = request.form.get("color", "#4285f4").strip()
    errors = {}
    if not name:
        errors["name"] = "Calendar name is required."

    if errors:
        return render_template("calendar_form.html", calendar=request.form.to_dict(), account=account, errors=errors)

    password = _get_credentials(account)
    if not password:
        return redirect(url_for("mail.login"))

    try:
        s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
        cal_url = caldav.create_calendar(s, _caldav_base_url(config), account.username, name=name, color=color)
        conn = _open_cache_for_account(account)
        try:
            import uuid as _uuid
            uid = str(_uuid.uuid4())
            cache_db.upsert_calendar(conn, uid, cal_url, displayname=name, color=color)
        finally:
            conn.close()
    except Exception:
        logger.exception("failed to create calendar on CalDAV")
        return render_template(
            "calendar_form.html",
            calendar=request.form.to_dict(),
            account=account,
            errors={"_server": "Failed to create calendar. Please check your connection and retry."},
        )

    return redirect(url_for("calendar.index"))


@calendar_bp.route("/calendar/calendars/<int:calendar_id>/edit", methods=["GET", "POST"])
@require_customer
def edit_calendar(calendar_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        cal = cache_db.get_calendar(conn, calendar_id)
        if not cal:
            return redirect(url_for("calendar.index"))

        if request.method == "GET":
            return render_template("calendar_form.html", calendar=cal, account=account, errors={})

        name = request.form.get("name", "").strip()
        color = request.form.get("color", "#4285f4").strip()
        errors = {}
        if not name:
            errors["name"] = "Calendar name is required."
        if errors:
            cal.update(request.form.to_dict())
            return render_template("calendar_form.html", calendar=cal, account=account, errors=errors)

        config = _get_caldav_config(account)
        password = _get_credentials(account)
        if config and password and cal.get("href"):
            try:
                s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
                caldav.update_calendar_props(s, cal["href"], displayname=name, color=color)
            except Exception:
                logger.exception("failed to update calendar props on CalDAV")

        cache_db.update_calendar(conn, calendar_id, displayname=name, color=color)
        return redirect(url_for("calendar.index"))
    finally:
        conn.close()


@calendar_bp.route("/calendar/calendars/<int:calendar_id>/delete", methods=["POST"])
@require_customer
def delete_calendar(calendar_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        cal = cache_db.get_calendar(conn, calendar_id)
        if not cal:
            return redirect(url_for("calendar.index"))

        if cal.get("is_default"):
            return redirect(url_for("calendar.index"))

        config = _get_caldav_config(account)
        password = _get_credentials(account)
        if config and password and cal.get("href"):
            try:
                s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
                caldav.delete_calendar(s, cal["href"])
            except Exception:
                logger.exception("failed to delete calendar from CalDAV")

        cache_db.delete_calendar_by_id(conn, calendar_id)
    finally:
        conn.close()

    return redirect(url_for("calendar.index"))


@calendar_bp.route("/calendar/calendars/<int:calendar_id>/toggle", methods=["POST"])
@require_customer
def toggle_calendar(calendar_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        cal = cache_db.get_calendar(conn, calendar_id)
        if cal:
            cache_db.update_calendar(conn, calendar_id, is_visible=not cal.get("is_visible", True))
    finally:
        conn.close()

    return redirect(url_for("calendar.index"))


@calendar_bp.route("/calendar/api/events/quick-create", methods=["POST"])
@require_customer
def api_quick_create():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"ok": False, "error": "No active account."}), 400

    data = request.get_json(silent=True) or {}
    summary = (data.get("summary") or "").strip() or "(no title)"
    dtstart = (data.get("dtstart") or "").strip()
    dtend = (data.get("dtend") or "").strip()
    calendar_id = data.get("calendar_id")
    all_day = bool(data.get("all_day"))
    browser_tz = (data.get("timezone") or "").strip()

    if not dtstart:
        return jsonify({"ok": False, "error": "Start time is required."}), 400
    if not calendar_id:
        return jsonify({"ok": False, "error": "Calendar is required."}), 400

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        return jsonify({"ok": False, "error": "CalDAV not configured."}), 400

    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"ok": False, "error": "Cache unavailable."}), 400

    try:
        cal = cache_db.get_calendar(conn, int(calendar_id))
        if not cal:
            return jsonify({"ok": False, "error": "Calendar not found."}), 404

        from app.modules.calendar.controllers.events import _get_user_timezone
        tz = browser_tz or _get_user_timezone(user_id)

        event_data = {
            "summary": summary,
            "dtstart": dtstart,
            "dtend": dtend or None,
            "all_day": all_day,
            "timezone": tz or None,
        }

        from app.modules.calendar.services.icalendar import generate_icalendar, extract_uid
        ical_text = generate_icalendar(event_data)
        uid = extract_uid(ical_text)

        password = _get_credentials(account)
        if not password:
            return jsonify({"ok": False, "error": "Credentials unavailable."}), 401

        s, _ = caldav.discover_calendars(
            _caldav_base_url(config), account.username, password
        )
        href, etag = caldav.create_event(s, cal["href"], ical_text, uid=uid)
        event_id = cache_db.upsert_event(
            conn, uid, href, etag, int(calendar_id), ical_text
        )

        return jsonify({"ok": True, "event_id": event_id})
    except Exception:
        logger.exception("quick create event failed")
        return jsonify({"ok": False, "error": "Failed to create event."}), 500
    finally:
        conn.close()


def _sync_calendars_and_events(conn, account, config):
    password = _get_credentials(account)
    if not password:
        return

    s, remote_calendars = caldav.discover_calendars(
        _caldav_base_url(config), account.username, password
    )

    if not remote_calendars:
        cal_name = _derive_default_calendar_name(account.username)
        try:
            cal_url = caldav.create_calendar(
                s, _caldav_base_url(config), account.username, name=cal_name, color="#4285f4"
            )
            remote_calendars = [{"url": cal_url, "displayname": cal_name, "color": "#4285f4", "sync_token": None}]
        except Exception:
            logger.exception("failed to auto-create default calendar for %s", account.username)
            return

    import uuid as _uuid
    from app.modules.calendar.services.icalendar import extract_uid

    local_cal_uids = set()
    for idx, rcal in enumerate(remote_calendars):
        cal_uid = _uuid.uuid5(_uuid.NAMESPACE_URL, rcal["url"]).hex
        local_cal_uids.add(cal_uid)
        is_default = idx == 0 and cache_db.count_calendars(conn) == 0
        cal_id = cache_db.upsert_calendar(
            conn,
            cal_uid,
            rcal["url"],
            displayname=rcal.get("displayname", "Calendar"),
            color=rcal.get("color", "#4285f4"),
            is_default=is_default,
        )

        try:
            remote_events = caldav.list_events(s, rcal["url"])
            remote_uids = set()
            for href, etag, ical_text in remote_events:
                uid = extract_uid(ical_text)
                if uid:
                    remote_uids.add(uid)
                    cache_db.upsert_event(conn, uid, href, etag, cal_id, ical_text)

            local_rows = conn.execute("SELECT uid FROM calendar_events WHERE calendar_id = ?", (cal_id,)).fetchall()
            for (local_uid,) in local_rows:
                if local_uid not in remote_uids:
                    cache_db.delete_event_by_uid(conn, local_uid, cal_id)
        except Exception:
            logger.exception("failed to sync events for calendar %s", rcal["url"])

        cache_db.set_sync_state(conn, rcal["url"], sync_token=rcal.get("sync_token"))

    local_cals = cache_db.get_all_calendars(conn)
    for lc in local_cals:
        if lc["uid"] not in local_cal_uids:
            cache_db.delete_calendar_by_id(conn, lc["id"])


def _derive_default_calendar_name(username):
    local_part = username.split("@")[0] if "@" in username else username
    parts = local_part.replace(".", " ").replace("_", " ").replace("-", " ").split()
    if parts:
        return parts[0][0].upper() + parts[0][1:].lower() if len(parts[0]) > 1 else parts[0].upper()
    return local_part.capitalize()


def _serialize_events(events):
    result = []
    for e in events:
        attendees = e.get("attendees")
        if isinstance(attendees, str):
            try:
                import json
                attendees = json.loads(attendees)
            except (ValueError, TypeError):
                attendees = None
        organizer = e.get("organizer")
        if isinstance(organizer, str):
            try:
                import json
                organizer = json.loads(organizer)
            except (ValueError, TypeError):
                organizer = None
        result.append({
            "id": e["id"],
            "uid": e["uid"],
            "summary": e.get("summary", ""),
            "description": e.get("description") or "",
            "location": e.get("location") or "",
            "dtstart": e.get("dtstart", ""),
            "dtend": e.get("dtend") or "",
            "all_day": bool(e.get("all_day")),
            "rrule": e.get("rrule") or "",
            "status": e.get("status", "CONFIRMED"),
            "calendar_id": e.get("calendar_id"),
            "calendar_color": e.get("calendar_color", "#4285f4"),
            "calendar_name": e.get("calendar_name", ""),
            "organizer": organizer,
            "attendees": attendees or [],
            "href": e.get("href", ""),
            "timezone": e.get("timezone") or "",
        })
    return result
