"""Web Push (VAPID) sender for new-mail notifications (U24.16 - U24.21).

Fire-and-forget by design: sends run on a small executor and every failure is
logged with identifiers; the sync/SSE path is never blocked or broken.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from flask import Flask

from app.shared import events
from app.shared.db import db
from app.shared.models.core import CustomerSettings, PushSubscription, PushVapidKey

_logger = logging.getLogger(__name__)

DEFAULT_VAPID_SUBJECT = "mailto:admin@localhost"

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webpush")

_app_ref: Flask | None = None
_subscriber_registered = False


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return _b64url(pub_raw), pem


def load_vapid_config() -> dict:
    """Return ``{"public_key", "private_key", "subject"}``.

    Env override (both keys must be set together), otherwise the DB keypair is
    used and auto-generated on first use. Misconfiguration raises a clear
    error instead of silently no-op'ing.
    """
    subject = os.environ.get("PUSH_VAPID_SUBJECT", DEFAULT_VAPID_SUBJECT)
    env_pub = os.environ.get("PUSH_VAPID_PUBLIC_KEY")
    env_priv = os.environ.get("PUSH_VAPID_PRIVATE_KEY")
    if env_pub and env_priv:
        return {"public_key": env_pub.strip(), "private_key": env_priv.strip(), "subject": subject}
    if env_pub or env_priv:
        raise RuntimeError(
            "PUSH_VAPID_PUBLIC_KEY and PUSH_VAPID_PRIVATE_KEY must be set together; "
            "one is missing. Set both or neither (a keypair is auto-generated)."
        )
    row = db.session.query(PushVapidKey).order_by(PushVapidKey.id).first()
    if row is None:
        pub, priv = _generate_keypair()
        row = PushVapidKey()
        row.public_key = pub
        row.private_key = priv
        db.session.add(row)
        db.session.commit()
        _logger.info("push vapid keypair generated")
    return {"public_key": row.public_key, "private_key": row.private_key, "subject": subject}


def _disable_subscription(sub: PushSubscription, status, error) -> None:
    sub.disabled_at = datetime.now(UTC)
    db.session.commit()
    _logger.info(
        "push subscription disabled user_id=%s subscription_id=%s status=%s error=%s",
        sub.user_id,
        sub.id,
        status,
        error,
    )


def _send_to_subscription(sub: PushSubscription, payload: dict, vapid: dict) -> None:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=vapid["private_key"],
            vapid_claims={"sub": vapid["subject"]},
            ttl=3600,
        )
        sub.last_used_at = datetime.now(UTC)
        db.session.commit()
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status = response.status_code if response is not None else None
        if status in (404, 410):
            _disable_subscription(sub, status, str(exc))
        else:
            _logger.warning(
                "push send failed user_id=%s subscription_id=%s status=%s",
                sub.user_id,
                sub.id,
                status,
            )
    except Exception:
        _logger.exception("push send error user_id=%s subscription_id=%s", sub.user_id, sub.id)


def _build_payload(count: int, detailed: bool, newest: dict | None) -> dict:
    if detailed and newest:
        subject = (newest.get("subject") or "").strip() or "(no subject)"
        sender = (newest.get("sender") or "").strip() or "New email"
        title = sender
        body = subject if count == 1 else f"{subject} (+{count - 1} more)"
    else:
        title = "New email"
        body = (
            "You have a new message in your inbox"
            if count == 1
            else f"{count} new messages in your inbox"
        )
    return {"title": title, "body": body, "tag": "lr-new-mail", "url": "/app/mail/"}


def send_new_mail_push(app: Flask, user_id: int, count: int, newest: dict | None) -> None:
    """Send a new-mail notification to every active device of the user."""
    with app.app_context():
        try:
            vapid = load_vapid_config()
        except Exception:
            _logger.exception("push vapid config invalid; push not sent user_id=%s", user_id)
            return
        subs = db.session.query(PushSubscription).filter_by(user_id=user_id, disabled_at=None).all()
        if not subs:
            return
        settings = db.session.get(CustomerSettings, user_id)
        detailed = bool(settings and settings.push_detailed)
        payload = _build_payload(count, detailed, newest)
        for sub in subs:
            _send_to_subscription(sub, payload, vapid)


def _on_event(user_id: int, event_type: str, data) -> None:
    if event_type != "new_mail" or not isinstance(data, dict):
        return
    app = _app_ref
    if app is None:
        return
    count = data.get("count")
    if not isinstance(count, int) or count <= 0:
        return
    newest = data.get("newest")
    _executor.submit(send_new_mail_push, app, user_id, count, newest)


def register_push(app: Flask) -> None:
    """Wire the new-mail event subscriber (called once from the app factory)."""
    global _app_ref, _subscriber_registered
    _app_ref = app
    if _subscriber_registered:
        return
    _subscriber_registered = True
    events.add_subscriber(_on_event)
    _logger.info("push subscriber registered")
