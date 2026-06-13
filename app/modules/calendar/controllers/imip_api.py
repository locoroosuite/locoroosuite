import json
import logging

from flask import request, jsonify, session

from app.shared.auth import require_customer
from app.modules.calendar.controllers.helpers import (
    calendar_bp,
    _get_account,
    _open_cache_for_account,
)
from app.modules.calendar.services import cache_db
from app.shared.db import db
from app.shared.models.core import Domain

logger = logging.getLogger(__name__)


@calendar_bp.route("/calendar/api/send-invite", methods=["POST"])
@require_customer
def send_invite():
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")
    method = (data.get("method") or "REQUEST").upper()

    if not event_id:
        return jsonify({"error": "Event ID is required."}), 400
    if method not in ("REQUEST", "CANCEL"):
        return jsonify({"error": "Invalid method."}), 400

    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"error": "No active account."}), 400

    account = _get_account(account_id, user_id)
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.is_active:
        return jsonify({"error": "Domain unavailable."}), 400

    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": "Cache unavailable."}), 400

    try:
        event = cache_db.get_event(conn, event_id)
        if not event:
            return jsonify({"error": "Event not found."}), 404

        attendees = []
        raw_attendees = event.get("attendees")
        if isinstance(raw_attendees, str):
            try:
                attendees = json.loads(raw_attendees)
            except (ValueError, TypeError):
                attendees = []
        elif isinstance(raw_attendees, list):
            attendees = raw_attendees

        if not attendees:
            return jsonify({"status": "ok", "message": "No attendees to notify."})

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
            "status": event.get("status", "CONFIRMED"),
            "organizer": {"cn": account.email_address, "email": account.email_address},
            "attendees": attendees,
        }

        from app.modules.calendar.services.imip import send_imip_email
        send_imip_email(domain, account, event_data, method, attendees, uid=event.get("uid"))

        return jsonify({"status": "ok"})
    except Exception:
        logger.exception("send-invite failed event_id=%s", event_id)
        return jsonify({"error": "Failed to send invitation."}), 500
    finally:
        conn.close()
