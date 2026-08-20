"""Web Push subscription management routes (U24.16, U24.22).

Session-authenticated JSON endpoints backing the Settings -> Notifications UI.
"""

import logging

from flask import jsonify, request, session

from app.modules.mail.controllers.helpers import _get_or_create_settings, mail_bp
from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.models.core import PushSubscription
from app.shared.push import load_vapid_config

_logger = logging.getLogger(__name__)

MAX_ENDPOINT_LEN = 512
MAX_KEY_LEN = 255


def _error(message: str, status: int):
    return jsonify({"error": {"code": "PUSH_INVALID", "message": message}}), status


@mail_bp.route("/mail/push/key", methods=["GET"])
@require_customer
def push_key():
    try:
        vapid = load_vapid_config()
    except Exception:
        _logger.exception("push vapid config invalid; cannot serve key")
        return _error(
            "Push notifications are misconfigured on the server. "
            "Contact your administrator (PUSH_VAPID_* settings).",
            503,
        )
    return jsonify({"public_key": vapid["public_key"]})


@mail_bp.route("/mail/push/subscribe", methods=["POST"])
@require_customer
def push_subscribe():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    endpoint = (payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return _error("endpoint, keys.p256dh and keys.auth are required.", 400)
    if len(endpoint) > MAX_ENDPOINT_LEN or len(p256dh) > MAX_KEY_LEN or len(auth) > MAX_KEY_LEN:
        return _error("Subscription fields exceed the maximum allowed length.", 400)
    user_agent = (request.headers.get("User-Agent") or "")[:255]
    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if existing is not None:
        existing.user_id = user_id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = user_agent
        existing.disabled_at = None
    else:
        row = PushSubscription()
        row.user_id = user_id
        row.endpoint = endpoint
        row.p256dh = p256dh
        row.auth = auth
        row.user_agent = user_agent
        db.session.add(row)
    db.session.commit()
    _logger.info("push subscription saved user_id=%s endpoint=%s", user_id, endpoint[:80])
    return jsonify({"status": "subscribed"})


@mail_bp.route("/mail/push/unsubscribe", methods=["POST"])
@require_customer
def push_unsubscribe():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    endpoint = (payload.get("endpoint") or "").strip()
    if not endpoint:
        return _error("endpoint is required.", 400)
    row = PushSubscription.query.filter_by(endpoint=endpoint, user_id=user_id).first()
    if row is None:
        return _error("Unknown subscription for this user.", 404)
    db.session.delete(row)
    db.session.commit()
    _logger.info("push subscription removed user_id=%s endpoint=%s", user_id, endpoint[:80])
    return jsonify({"status": "unsubscribed"})


@mail_bp.route("/mail/push/devices", methods=["GET"])
@require_customer
def push_devices():
    user_id = session.get("user_id")
    rows = (
        PushSubscription.query.filter_by(user_id=user_id, disabled_at=None)
        .order_by(PushSubscription.created_at.desc())
        .all()
    )
    return jsonify(
        {
            "devices": [
                {
                    "id": row.id,
                    "user_agent": row.user_agent,
                    "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                }
                for row in rows
            ]
        }
    )


@mail_bp.route("/mail/push/devices/<int:device_id>/remove", methods=["POST"])
@require_customer
def push_device_remove(device_id: int):
    user_id = session.get("user_id")
    row = PushSubscription.query.filter_by(id=device_id, user_id=user_id).first()
    if row is None:
        return _error("Unknown device for this user.", 404)
    db.session.delete(row)
    db.session.commit()
    _logger.info("push device removed user_id=%s device_id=%s", user_id, device_id)
    return jsonify({"status": "removed"})


@mail_bp.route("/mail/push/detailed", methods=["POST"])
@require_customer
def push_detailed():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("enabled"), bool):
        return _error("'enabled' boolean is required.", 400)
    settings = _get_or_create_settings(user_id)
    settings.push_detailed = payload["enabled"]
    db.session.commit()
    return jsonify({"status": "saved", "push_detailed": settings.push_detailed})
