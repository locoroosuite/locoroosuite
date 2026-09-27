"""Delivery/read status computation for chat messages (HLD U25.18).

Two independent signals feed the sender-visible ticks:

- Read (blue double tick): peers' public ``m.read`` receipts, observed via
  the sender's own Matrix sync (ephemeral ``m.receipt`` events) and
  persisted in the per-user chat cache (``chat_receipts``).
- Delivered (grey double tick): app-internal tracking. Matrix has no
  delivery receipt, and Synapse 1.161 rejects custom receipt types while
  not distributing ``m.read.private`` to the event sender (verified), so
  the recipient's server-side sync loop records ingestion of foreign DM
  messages in the shared app DB table ``chat_message_delivery``.

Delivery rows are accessed through ``db.engine`` (never ``db.session``)
because the chat SSE stream generator runs outside the Flask application
context.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.modules.chat.services import cache_db
from app.shared.db import db
from app.shared.models.core import ChatMessageDelivery

logger = logging.getLogger(__name__)

RECEIPT_TYPE_READ = "m.read"

STATUS_SENT = "sent"
STATUS_DELIVERED = "delivered"
STATUS_READ = "read"


def extract_read_receipts(ephemeral_events: list[dict], own_user_id: str) -> list[dict]:
    """Parse ``m.receipt`` ephemeral events into peer read receipts.

    Only public ``m.read`` receipts from other users are returned: own
    receipts are meaningless for the local user, and private receipts are
    not distributed by Synapse (verified, U25.18).
    """
    receipts: list[dict] = []
    for event in ephemeral_events or []:
        if event.get("type") != "m.receipt":
            continue
        content = event.get("content") or {}
        for event_id, by_type in content.items():
            read = by_type.get(RECEIPT_TYPE_READ) if isinstance(by_type, dict) else None
            for user_id, meta in (read or {}).items():
                if user_id == own_user_id or not event_id:
                    continue
                ts = meta.get("ts") if isinstance(meta, dict) else None
                receipts.append(
                    {
                        "user_id": user_id,
                        "event_id": event_id,
                        "ts": int(ts) if isinstance(ts, (int, float)) else 0,
                    }
                )
    return receipts


def receipts_payload(conn, room_id: str) -> list[dict]:
    """API shape of the cached receipts for one room."""
    return [
        {
            "user_id": r["user_id"],
            "receipt_type": r["receipt_type"],
            "event_id": r["event_id"],
            "ts": r["receipt_ts"],
        }
        for r in cache_db.list_receipts(conn, room_id)
    ]


def _peer_read_coverage(conn, room_id: str) -> dict[str, int]:
    """Latest covered timeline timestamp per peer from cached read receipts.

    The receipt's target event is looked up in the message cache for its
    ``origin_server_ts``; when the target is not cached (receipt for an old
    event outside the cache window) the receipt's own timestamp is used —
    it was necessarily posted after every message it covers.
    """
    coverage: dict[str, int] = {}
    for receipt in cache_db.list_receipts(conn, room_id):
        if receipt["receipt_type"] != RECEIPT_TYPE_READ:
            continue
        target = cache_db.get_message(conn, receipt["event_id"])
        ts = target["origin_server_ts"] if target else receipt["receipt_ts"]
        if ts > coverage.get(receipt["user_id"], -1):
            coverage[receipt["user_id"]] = int(ts)
    return coverage


# --- delivery recording (recipient side) ---------------------------------


def record_deliveries(conn, own_matrix_id: str, changes: dict) -> int:
    """Record app-internal delivery rows for ingested foreign DM messages.

    Runs on the RECIPIENT's side after ``process_sync`` (or a backfill): a
    message someone else sent in a DM room is delivered to us the moment
    our server-side sync ingests it. Idempotent per (event_id, recipient).
    Never raises into the sync loop: failures are logged with context and
    the affected room is skipped (the next sync re-attempts).
    """
    recorded = 0
    messages_by_room: dict[str, list[dict]] = changes.get("messages") or {}
    for room_id, messages in messages_by_room.items():
        try:
            room = cache_db.get_room(conn, room_id)
        except Exception:
            logger.exception("chat delivery: room lookup failed room_id=%s", room_id)
            continue
        if not room or not room.get("is_direct"):
            continue
        foreign = [m for m in messages if m.get("sender") and m["sender"] != own_matrix_id]
        if not foreign:
            continue
        try:
            recorded += _insert_delivery_rows(room_id, own_matrix_id, foreign)
        except Exception:
            logger.exception(
                "chat delivery: recording failed room_id=%s recipient=%s count=%s",
                room_id,
                own_matrix_id,
                len(foreign),
            )
    return recorded


def record_backfill_deliveries(conn, own_matrix_id: str, room_id: str, rows: list[dict]) -> int:
    """Delivery recording for messages ingested via timeline backfill."""
    room = cache_db.get_room(conn, room_id)
    if not room or not room.get("is_direct"):
        return 0
    foreign = [r for r in rows if r.get("sender") and r["sender"] != own_matrix_id]
    if not foreign:
        return 0
    try:
        return _insert_delivery_rows(room_id, own_matrix_id, foreign)
    except Exception:
        logger.exception(
            "chat delivery: backfill recording failed room_id=%s recipient=%s count=%s",
            room_id,
            own_matrix_id,
            len(foreign),
        )
        return 0


def _insert_delivery_rows(room_id: str, recipient: str, messages: list[dict]) -> int:
    stmt = sqlite_insert(ChatMessageDelivery).values(
        [
            {
                "event_id": m["event_id"],
                "room_id": room_id,
                "sender_matrix_id": m["sender"],
                "recipient_matrix_id": recipient,
                "delivered_at": datetime.now(UTC),
            }
            for m in messages
        ]
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "recipient_matrix_id"])
    with db.engine.begin() as engine_conn:
        result = engine_conn.execute(stmt)
        return int(result.rowcount or 0)


# --- delivery queries (sender side) --------------------------------------


def delivered_event_ids(room_id: str, sender_matrix_id: str, event_ids: list[str]) -> set[str]:
    """Which of ``event_ids`` (sent by ``sender_matrix_id``) are delivered."""
    if not event_ids:
        return set()
    stmt = select(ChatMessageDelivery.event_id).where(
        ChatMessageDelivery.room_id == room_id,
        ChatMessageDelivery.sender_matrix_id == sender_matrix_id,
        ChatMessageDelivery.event_id.in_(event_ids),
    )
    with db.engine.connect() as engine_conn:
        return set(engine_conn.execute(stmt).scalars())


def new_deliveries_for_sender(
    sender_matrix_id: str, since: datetime
) -> tuple[list[dict], datetime]:
    """Delivery rows for a sender newer than ``since`` (SSE relay deltas).

    Returns (payload, new_marker); the marker is the max ``delivered_at``
    seen, or ``since`` unchanged when there is nothing new.
    """
    stmt = (
        select(
            ChatMessageDelivery.room_id,
            ChatMessageDelivery.event_id,
            ChatMessageDelivery.delivered_at,
        )
        .where(
            ChatMessageDelivery.sender_matrix_id == sender_matrix_id,
            ChatMessageDelivery.delivered_at > since,
        )
        .order_by(ChatMessageDelivery.delivered_at)
    )
    with db.engine.connect() as engine_conn:
        rows = engine_conn.execute(stmt).all()
    if not rows:
        return [], since
    payload = [{"room_id": row.room_id, "event_id": row.event_id} for row in rows]
    marker = rows[-1].delivered_at
    # SQLite returns naive datetimes; treat stored values as UTC.
    if marker.tzinfo is None:
        marker = marker.replace(tzinfo=UTC)
    return payload, marker


# --- status computation (all API surfaces) -------------------------------


def decorate_statuses(conn, room: dict, messages: list[dict], own_id: str) -> None:
    """Set ``status`` on each message dict (mutates in place).

    Own, non-redacted messages in direct-message rooms get
    ``sent``/``delivered``/``read``; every other message gets ``None``
    (group rooms show no ticks by design, U25.18).
    """
    if not room.get("is_direct"):
        for message in messages:
            message["status"] = None
        return

    coverage = _peer_read_coverage(conn, room["room_id"])
    own = [m for m in messages if m.get("sender") == own_id and not m.get("redacted")]
    delivered = delivered_event_ids(room["room_id"], own_id, [m["event_id"] for m in own])
    for message in messages:
        if message.get("sender") != own_id or message.get("redacted"):
            message["status"] = None
            continue
        is_read = any(message["origin_server_ts"] <= ts for ts in coverage.values())
        if is_read:
            message["status"] = STATUS_READ
        elif message["event_id"] in delivered:
            message["status"] = STATUS_DELIVERED
        else:
            message["status"] = STATUS_SENT
