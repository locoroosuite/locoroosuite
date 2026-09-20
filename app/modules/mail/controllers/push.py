"""Web Push subscription management routes (U24.16, U24.22, U24.27-U24.29).

Session-authenticated JSON endpoints backing the Settings -> Notifications UI.
"""

import logging

from flask import current_app, jsonify, request, session

from app.modules.mail.controllers.helpers import _get_or_create_settings, mail_bp
from app.shared import push as push_service
from app.shared import push_keys
from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.models.core import PushSubscription
from app.shared.push import load_vapid_config

_logger = logging.getLogger(__name__)

MAX_ENDPOINT_LEN = 512
MAX_KEY_LEN = 255


def _error(message: str, status: int):
    return jsonify({"error": {"code": "PUSH_INVALID", "message": message}}), status


def _has_active_subscription(user_id: int) -> bool:
    return PushSubscription.query.filter_by(user_id=user_id, disabled_at=None).first() is not None


def _maybe_clear_push_key_store(user_id: int) -> None:
    """Drop the wrapped DEK when the last subscription is gone (U24.29)."""
    if _has_active_subscription(user_id):
        return
    if push_keys.clear_push_dek(user_id):
        sync_manager = getattr(current_app, "sync_manager", None)
        if sync_manager is not None:
            sync_manager.disarm_push_user(user_id)


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
    # U24.29/U24.30: arming — persist the wrapped DEK and start headless sync
    # for every account of this user. Failures are logged, never fatal: the
    # subscription itself is valid and in-app push still works.
    if user_id is not None:
        try:
            push_keys.store_push_dek_if_subscribed(user_id)
        except Exception:
            _logger.exception("push key store save failed user_id=%s", user_id)
    sync_manager = getattr(current_app, "sync_manager", None)
    if sync_manager is not None and user_id is not None:
        try:
            sync_manager.arm_push_user(user_id)
        except Exception:
            _logger.exception("push arm failed user_id=%s", user_id)
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
    if user_id is not None:
        _maybe_clear_push_key_store(user_id)
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
    if user_id is not None:
        _maybe_clear_push_key_store(user_id)
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


@mail_bp.route("/mail/push/category", methods=["POST"])
@require_customer
def push_category():
    """Per-category notification toggle (U24.27)."""
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    category = payload.get("category")
    if category not in push_service.CATEGORY_SETTINGS:
        return _error(
            "'category' must be one of: " + ", ".join(sorted(push_service.CATEGORY_SETTINGS)) + ".",
            400,
        )
    if not isinstance(payload.get("enabled"), bool):
        return _error("'enabled' boolean is required.", 400)
    settings = _get_or_create_settings(user_id)
    setattr(settings, push_service.CATEGORY_SETTINGS[category], payload["enabled"])
    db.session.commit()
    _logger.info(
        "push category toggled user_id=%s category=%s enabled=%s",
        user_id,
        category,
        payload["enabled"],
    )
    return jsonify({"status": "saved", "category": category, "enabled": payload["enabled"]})


@mail_bp.route("/mail/push/test", methods=["POST"])
@require_customer
def push_test():
    """Send a benign test notification to every active device (U24.28)."""
    user_id = session.get("user_id")
    if user_id is None:
        return _error("Unknown user.", 401)
    if not _has_active_subscription(user_id):
        return (
            jsonify(
                {
                    "error": {
                        "code": "PUSH_NO_SUBSCRIPTION",
                        "message": "No registered device yet. Enable notifications on this device first, then retry.",
                    }
                }
            ),
            400,
        )
    sent = push_service.send_test_push(None, user_id)
    if not sent:
        return (
            jsonify(
                {
                    "error": {
                        "code": "PUSH_SEND_FAILED",
                        "message": "The test notification could not be delivered. Check the server push configuration (PUSH_VAPID_* settings) and retry.",
                    }
                }
            ),
            503,
        )
    return jsonify({"status": "sent"})
