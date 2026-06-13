import json
import logging

from flask import request, jsonify, session

from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.models.core import Domain
from app.modules.calendar.controllers.helpers import (
    calendar_bp,
    _get_account,
    _get_caldav_config,
    _get_credentials,
    _open_cache_for_account,
    _caldav_base_url,
)
from app.modules.calendar.services import caldav, cache_db
from app.shared.icalendar import parse_icalendar, generate_icalendar, extract_uid

logger = logging.getLogger(__name__)


@calendar_bp.route("/calendar/api/calendars")
@require_customer
def api_calendars():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify([])
    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify([])
    try:
        rows = cache_db.get_all_calendars(conn)
        return jsonify([{"id": r["id"], "displayname": r["displayname"], "color": r.get("color", "#3B82F6")} for r in rows])
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/ics-parse", methods=["POST"])
@require_customer
def ics_parse():
    data = request.get_json(silent=True) or {}
    ical_text = data.get("ical_text", "")
    if not ical_text:
        return jsonify({"error": "No .ics content provided."}), 400

    parsed = parse_icalendar(ical_text)
    if not parsed:
        return jsonify({"error": "Could not parse .ics content."}), 400

    method = parsed.get("method", "").upper()
    uid = parsed.get("uid", "")

    result = {
        "uid": uid,
        "summary": parsed.get("summary", ""),
        "description": parsed.get("description", ""),
        "location": parsed.get("location", ""),
        "dtstart": parsed.get("dtstart", ""),
        "dtend": parsed.get("dtend", ""),
        "all_day": parsed.get("all_day", False),
        "organizer": parsed.get("organizer"),
        "attendees": parsed.get("attendees", []),
        "status": parsed.get("status", "CONFIRMED"),
        "method": method,
        "sequence": parsed.get("sequence", 0),
        "is_invitation": method == "REQUEST",
        "is_cancellation": method == "CANCEL",
        "is_publish": method in ("PUBLISH", "") or method == "",
    }

    if uid:
        account_id = session.get("active_account_id")
        user_id = session.get("user_id")
        if account_id:
            try:
                account = _get_account(account_id, user_id)
                conn = _open_cache_for_account(account)
                if conn:
                    try:
                        existing = cache_db.get_event_by_uid(conn, uid)
                        result["already_imported"] = existing is not None
                        if existing:
                            result["existing_event_id"] = existing["id"]
                            result["existing_calendar_id"] = existing.get("calendar_id")
                            result["existing_status"] = existing.get("status", "CONFIRMED")
                            existing_attendees = existing.get("attendees")
                            if isinstance(existing_attendees, str):
                                try:
                                    existing_attendees = json.loads(existing_attendees)
                                except (ValueError, TypeError):
                                    existing_attendees = []
                            if existing_attendees:
                                my_email = account.email_address.lower()
                                for att in existing_attendees:
                                    if att.get("email", "").lower() == my_email:
                                        result["existing_my_partstat"] = att.get("partstat", "NEEDS-ACTION")
                                        break
                    finally:
                        conn.close()
            except Exception:
                logger.debug("could not check existing event for uid=%s", uid, exc_info=True)

    return jsonify(result)


@calendar_bp.route("/calendar/api/conflicts")
@require_customer
def ics_conflicts():
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    if not start or not end:
        return jsonify([])

    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify([])

    account = _get_account(account_id, user_id)
    conn = None
    try:
        conn = _open_cache_for_account(account)
    except Exception:
        logger.debug("conflicts cache open failed", exc_info=True)
    if not conn:
        return jsonify([])

    try:
        conflicts = cache_db.get_conflicting_events(conn, start, end)
        return jsonify(conflicts)
    except Exception:
        logger.exception("conflicts check failed")
        return jsonify([])
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/ics-import", methods=["POST"])
@require_customer
def ics_import():
    data = request.get_json(silent=True) or {}
    ical_text = data.get("ical_text", "")
    calendar_id = data.get("calendar_id")
    source_email_message_id = data.get("source_email_message_id")
    source_email_account_id = data.get("source_email_account_id")
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")

    if not ical_text:
        logger.warning("ics-import: missing ical_text account_id=%s", account_id)
        return jsonify({"error": "No invitation data found. Try reloading the email and importing again."}), 400
    if not calendar_id:
        logger.warning("ics-import: missing calendar_id account_id=%s", account_id)
        return jsonify({"error": "No calendar selected. Please select a calendar and try again."}), 400
    if not account_id:
        logger.warning("ics-import: no active_account_id in session user_id=%s", user_id)
        return jsonify({"error": "No active email account. Please refresh the page and try again."}), 400

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        logger.warning("ics-import: caldav not configured account_id=%s", account_id)
        return jsonify({"error": "Calendar is not configured for your account. Please contact your administrator."}), 400

    conn = _open_cache_for_account(account)
    if not conn:
        logger.warning("ics-import: cache unavailable account_id=%s", account_id)
        return jsonify({"error": "Calendar data could not be loaded. Please refresh the page and try again."}), 400

    try:
        cal = cache_db.get_calendar(conn, calendar_id)
        if not cal:
            return jsonify({"error": "Calendar not found."}), 404

        parsed = parse_icalendar(ical_text)
        uid = parsed.get("uid") or extract_uid(ical_text)

        password = _get_credentials(account)
        if not password:
            logger.warning("ics-import: credentials unavailable account_id=%s", account_id)
            return jsonify({"error": "Could not access your calendar credentials. Please refresh the page and try again."}), 401

        import_data = {k: v for k, v in parsed.items() if k != "method"}
        import_data["method"] = None
        clean_ical = generate_icalendar(import_data, uid=uid)

        try:
            s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
            href, etag = caldav.create_event(s, cal["href"], clean_ical, uid=uid)
        except Exception:
            logger.exception("ics-import: failed to save to caldav account_id=%s calendar_id=%s", account_id, calendar_id)
            return jsonify({"error": "Could not save the event to your calendar. Please check your calendar connection and try again, or contact your administrator."}), 500

        event_id = cache_db.upsert_event(conn, uid, href, etag, calendar_id, clean_ical)

        if source_email_message_id and source_email_account_id:
            cache_db.set_event_source_email(conn, event_id, source_email_message_id, source_email_account_id)

        event = cache_db.get_event(conn, event_id)
        return jsonify({
            "status": "ok",
            "event_id": event_id,
            "uid": uid,
            "summary": event.get("summary", "") if event else "",
            "calendar_event_url": f"/app/calendar/events/{event_id}" if event_id else None,
        })
    except Exception:
        logger.exception("ics-import: unexpected failure account_id=%s", account_id)
        return jsonify({"error": "An unexpected error occurred while importing the event. Please refresh the page and try again."}), 500
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/ics-rsvp", methods=["POST"])
@require_customer
def ics_rsvp():
    data = request.get_json(silent=True) or {}
    ical_text = data.get("ical_text", "")
    calendar_id = data.get("calendar_id")
    partstat = data.get("partstat", "ACCEPTED").upper()
    source_email_message_id = data.get("source_email_message_id")
    source_email_account_id = data.get("source_email_account_id")
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    logger.info("ics-rsvp: calendar_id=%s partstat=%s msg_id=%s account_id=%s", calendar_id, partstat, source_email_message_id, account_id)

    if not ical_text:
        logger.warning("ics-rsvp: missing ical_text account_id=%s", account_id)
        return jsonify({"error": "No invitation data found. Try reloading the email and responding again."}), 400
    if not calendar_id:
        logger.warning("ics-rsvp: missing calendar_id account_id=%s", account_id)
        return jsonify({"error": "No calendar selected. Please select a calendar and try again."}), 400
    if partstat not in ("ACCEPTED", "TENTATIVE", "DECLINED"):
        logger.warning("ics-rsvp: invalid partstat=%s account_id=%s", partstat, account_id)
        return jsonify({"error": "Invalid response status. Please try again."}), 400
    if not account_id:
        logger.warning("ics-rsvp: no active_account_id in session user_id=%s", user_id)
        return jsonify({"error": "No active email account. Please refresh the page and try again."}), 400

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        logger.warning("ics-rsvp: caldav not configured account_id=%s", account_id)
        return jsonify({"error": "Calendar is not configured for your account. Please contact your administrator."}), 400

    conn = _open_cache_for_account(account)
    if not conn:
        logger.warning("ics-rsvp: cache unavailable account_id=%s", account_id)
        return jsonify({"error": "Calendar data could not be loaded. Please refresh the page and try again."}), 400

    try:
        cal = cache_db.get_calendar(conn, calendar_id)
        if not cal:
            return jsonify({"error": "Calendar not found."}), 404

        parsed = parse_icalendar(ical_text)
        uid = parsed.get("uid") or extract_uid(ical_text)
        if not uid:
            return jsonify({"error": "No UID in .ics."}), 400

        attendees = parsed.get("attendees", [])
        my_email = account.email_address.lower()
        updated_attendees = []
        for att in attendees:
            if att.get("email", "").lower() == my_email:
                updated_attendees.append({**att, "partstat": partstat, "rsvp": "FALSE"})
            else:
                updated_attendees.append(att)

        import_data = {k: v for k, v in parsed.items() if k not in ("method", "attendees")}
        import_data["attendees"] = updated_attendees
        if partstat == "DECLINED":
            import_data["status"] = "CANCELLED"
        clean_ical = generate_icalendar(import_data, uid=uid)

        password = _get_credentials(account)
        if not password:
            logger.warning("ics-rsvp: credentials unavailable account_id=%s", account_id)
            return jsonify({"error": "Could not access your calendar credentials. Please refresh the page and try again."}), 401

        try:
            s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
            href, etag = caldav.create_event(s, cal["href"], clean_ical, uid=uid)
        except Exception:
            logger.exception("ics-rsvp: failed to save to caldav account_id=%s calendar_id=%s uid=%s", account_id, calendar_id, uid)
            return jsonify({"error": "Could not save the event to your calendar. Please check your calendar connection and try again, or contact your administrator."}), 500

        event_id = cache_db.upsert_event(conn, uid, href, etag, calendar_id, clean_ical)

        if source_email_message_id and source_email_account_id:
            cache_db.set_event_source_email(conn, event_id, source_email_message_id, source_email_account_id)

        try:
            organizer = parsed.get("organizer")
            if organizer and organizer.get("email"):
                my_attendee = None
                for att in attendees:
                    if att.get("email", "").lower() == my_email:
                        my_attendee = att
                        break
                if my_attendee:
                    my_attendee_reply = dict(my_attendee)
                    my_attendee_reply["partstat"] = partstat
                    my_attendee_reply["rsvp"] = "FALSE"
                    reply_event_data = {
                        "summary": parsed.get("summary", ""),
                        "description": parsed.get("description"),
                        "location": parsed.get("location"),
                        "dtstart": parsed.get("dtstart"),
                        "dtend": parsed.get("dtend"),
                        "all_day": parsed.get("all_day"),
                        "timezone": parsed.get("timezone"),
                        "uid": uid,
                        "sequence": parsed.get("sequence", 0),
                        "status": parsed.get("status", "CONFIRMED"),
                        "organizer": organizer,
                        "attendees": [my_attendee_reply],
                    }
                    from app.modules.calendar.services.imip import send_reply_imip
                    send_reply_imip(
                        db.session.get(Domain, account.domain_id),
                        account,
                        my_attendee_reply,
                        organizer,
                        reply_event_data,
                        partstat,
                        uid=uid,
                    )
        except Exception:
            logger.exception("ics-rsvp: failed to send reply email uid=%s account_id=%s", uid, account_id)

        return jsonify({
            "status": "ok",
            "event_id": event_id,
            "uid": uid,
            "partstat": partstat,
            "calendar_event_url": f"/app/calendar/events/{event_id}" if event_id else None,
        })
    except Exception:
        logger.exception("ics-rsvp: unexpected failure account_id=%s", account_id)
        return jsonify({"error": "An unexpected error occurred while responding to the invitation. Please refresh the page and try again."}), 500
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/ics-rsvp-existing", methods=["POST"])
@require_customer
def ics_rsvp_existing():
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")
    partstat = data.get("partstat", "ACCEPTED").upper()
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    logger.info("ics-rsvp-existing: event_id=%s partstat=%s account_id=%s", event_id, partstat, account_id)

    if not event_id:
        logger.warning("ics-rsvp-existing: missing event_id account_id=%s", account_id)
        return jsonify({"error": "No event specified. Please refresh the page and try again."}), 400
    if partstat not in ("ACCEPTED", "TENTATIVE", "DECLINED"):
        logger.warning("ics-rsvp-existing: invalid partstat=%s account_id=%s", partstat, account_id)
        return jsonify({"error": "Invalid response status. Please try again."}), 400
    if not account_id:
        logger.warning("ics-rsvp-existing: no active_account_id in session user_id=%s", user_id)
        return jsonify({"error": "No active email account. Please refresh the page and try again."}), 400

    account = _get_account(account_id, user_id)
    config = _get_caldav_config(account)
    if not config:
        logger.warning("ics-rsvp-existing: caldav not configured account_id=%s", account_id)
        return jsonify({"error": "Calendar is not configured for your account. Please contact your administrator."}), 400

    conn = _open_cache_for_account(account)
    if not conn:
        logger.warning("ics-rsvp-existing: cache unavailable account_id=%s", account_id)
        return jsonify({"error": "Calendar data could not be loaded. Please refresh the page and try again."}), 400

    try:
        event = cache_db.get_event(conn, event_id)
        if not event:
            return jsonify({"error": "Event not found."}), 404

        ical_text = event.get("raw_ical", "")
        if not ical_text:
            logger.warning("ics-rsvp-existing: no raw_ical for event_id=%s", event_id)
            return jsonify({"error": "Could not load event data. Please try again."}), 400

        calendar_id = event["calendar_id"]
        cal = cache_db.get_calendar(conn, calendar_id)
        if not cal:
            return jsonify({"error": "Calendar not found."}), 404

        parsed = parse_icalendar(ical_text)
        uid = parsed.get("uid") or extract_uid(ical_text)
        if not uid:
            return jsonify({"error": "No UID in event data."}), 400

        attendees = parsed.get("attendees", [])
        my_email = account.email_address.lower()
        updated_attendees = []
        for att in attendees:
            if att.get("email", "").lower() == my_email:
                updated_attendees.append({**att, "partstat": partstat, "rsvp": "FALSE"})
            else:
                updated_attendees.append(att)

        import_data = {k: v for k, v in parsed.items() if k not in ("method", "attendees")}
        import_data["attendees"] = updated_attendees
        if partstat == "DECLINED":
            import_data["status"] = "CANCELLED"
        else:
            import_data["status"] = "CONFIRMED"
        clean_ical = generate_icalendar(import_data, uid=uid)

        password = _get_credentials(account)
        if not password:
            logger.warning("ics-rsvp-existing: credentials unavailable account_id=%s", account_id)
            return jsonify({"error": "Could not access your calendar credentials. Please refresh the page and try again."}), 401

        try:
            s, _ = caldav.discover_calendars(_caldav_base_url(config), account.username, password)
            href, etag = caldav.create_event(s, cal["href"], clean_ical, uid=uid)
        except Exception:
            logger.exception("ics-rsvp-existing: failed to save to caldav event_id=%s uid=%s", event_id, uid)
            return jsonify({"error": "Could not update the event. Please check your calendar connection and try again."}), 500

        new_event_id = cache_db.upsert_event(conn, uid, href, etag, calendar_id, clean_ical)

        source_msg = event.get("source_email_message_id")
        source_acc = event.get("source_email_account_id")
        if source_msg and source_acc:
            cache_db.set_event_source_email(conn, new_event_id, source_msg, source_acc)

        try:
            organizer = parsed.get("organizer")
            if organizer and organizer.get("email"):
                my_attendee = None
                for att in attendees:
                    if att.get("email", "").lower() == my_email:
                        my_attendee = att
                        break
                if my_attendee:
                    my_attendee_reply = dict(my_attendee)
                    my_attendee_reply["partstat"] = partstat
                    my_attendee_reply["rsvp"] = "FALSE"
                    reply_event_data = {
                        "summary": parsed.get("summary", ""),
                        "description": parsed.get("description"),
                        "location": parsed.get("location"),
                        "dtstart": parsed.get("dtstart"),
                        "dtend": parsed.get("dtend"),
                        "all_day": parsed.get("all_day"),
                        "timezone": parsed.get("timezone"),
                        "uid": uid,
                        "sequence": parsed.get("sequence", 0),
                        "status": parsed.get("status", "CONFIRMED"),
                        "organizer": organizer,
                        "attendees": [my_attendee_reply],
                    }
                    from app.modules.calendar.services.imip import send_reply_imip
                    send_reply_imip(
                        db.session.get(Domain, account.domain_id),
                        account,
                        my_attendee_reply,
                        organizer,
                        reply_event_data,
                        partstat,
                        uid=uid,
                    )
        except Exception:
            logger.exception("ics-rsvp-existing: failed to send reply email uid=%s account_id=%s", uid, account_id)

        return jsonify({
            "status": "ok",
            "event_id": new_event_id,
            "uid": uid,
            "partstat": partstat,
            "calendar_event_url": f"/app/calendar/events/{new_event_id}" if new_event_id else None,
        })
    except Exception:
        logger.exception("ics-rsvp-existing: unexpected failure event_id=%s account_id=%s", event_id, account_id)
        return jsonify({"error": "An unexpected error occurred. Please refresh the page and try again."}), 500
    finally:
        conn.close()


@calendar_bp.route("/calendar/api/ics-cancel", methods=["POST"])
@require_customer
def ics_cancel():
    data = request.get_json(silent=True) or {}
    ical_text = data.get("ical_text", "")

    if not ical_text:
        logger.warning("ics-cancel: missing ical_text")
        return jsonify({"error": "No invitation data found. Try reloading the email."}), 400

    parsed = parse_icalendar(ical_text)
    uid = parsed.get("uid") or extract_uid(ical_text)
    if not uid:
        logger.warning("ics-cancel: no uid in ical_text")
        return jsonify({"error": "Could not identify the event. Please try again."}), 400

    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        logger.warning("ics-cancel: no active_account_id in session user_id=%s", user_id)
        return jsonify({"error": "No active email account. Please refresh the page and try again."}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        logger.warning("ics-cancel: cache unavailable account_id=%s", account_id)
        return jsonify({"error": "Calendar data could not be loaded. Please refresh the page and try again."}), 400

    try:
        event = cache_db.get_event_by_uid(conn, uid)
        if not event:
            return jsonify({"status": "ok", "action": "not_found", "message": "Event not in calendar."})

        conn.execute(
            "UPDATE calendar_events SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
            (cache_db._now(), event["id"]),
        )
        conn.commit()

        return jsonify({
            "status": "ok",
            "action": "cancelled",
            "event_id": event["id"],
            "summary": event.get("summary", ""),
            "calendar_event_url": f"/app/calendar/events/{event['id']}",
        })
    except Exception:
        logger.exception("ics-cancel: unexpected failure uid=%s account_id=%s", uid, account_id)
        return jsonify({"error": "An unexpected error occurred. Please refresh the page and try again."}), 500
    finally:
        conn.close()
