"""Process Matrix /sync responses into the chat cache.

Returns a serializable change summary used for SSE push to the browser.
All cache writes are idempotent (event ids are primary keys), so multiple
browser tabs running sync loops on the same account are safe.
"""

from __future__ import annotations

import json
import logging
import threading

from app.modules.chat.services import cache_db, receipts

logger = logging.getLogger(__name__)

_user_sync_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(user_key: int) -> threading.Lock:
    with _locks_guard:
        if user_key not in _user_sync_locks:
            _user_sync_locks[user_key] = threading.Lock()
        return _user_sync_locks[user_key]


def sync_lock(user_id: int) -> threading.Lock:
    """Per-user lock serializing cache writes across concurrent sync loops."""
    return _lock_for(user_id)


def process_sync(conn, own_user_id: str, response: dict) -> dict:
    """Apply a /sync response to the cache; returns SSE-friendly changes."""
    changes: dict = {
        "rooms_upserted": [],
        "rooms_removed": [],
        "messages": {},
        "typing": {},
        "receipts": {},
        "calls": {},
    }
    rooms = response.get("rooms", {})

    for room_id, data in rooms.get("invite", {}).items():
        _process_invite(conn, room_id, data, changes)
    for room_id, _data in rooms.get("leave", {}).items():
        cache_db.update_room_fields(conn, room_id, {"membership": "leave"})
        changes["rooms_removed"].append(room_id)
    for room_id, data in rooms.get("join", {}).items():
        _process_joined(conn, own_user_id, room_id, data, changes)

    cache_db.set_sync_state(conn, response.get("next_batch"))
    return changes


def _process_invite(conn, room_id: str, data: dict, changes: dict) -> None:
    cache_db.upsert_room(conn, room_id, membership="invite")
    for event in data.get("invite_state", {}).get("events", []):
        _apply_state_event(conn, room_id, event, "", changes)
    changes["rooms_upserted"].append(room_id)


def _process_joined(conn, own_user_id: str, room_id: str, data: dict, changes: dict) -> None:
    existed = cache_db.get_room(conn, room_id)
    cache_db.upsert_room(conn, room_id, membership="join")

    state = data.get("state", {})
    unread = data.get("unread_notifications", {})
    summary = data.get("summary", {})
    timeline = data.get("timeline", {})
    ephemeral = data.get("ephemeral", {})
    account_data = data.get("account_data", {})

    room_updates: dict = {}
    if unread.get("notification_count") is not None:
        room_updates["notification_count"] = int(unread.get("notification_count") or 0)
    if unread.get("highlight_count") is not None:
        room_updates["highlight_count"] = int(unread.get("highlight_count") or 0)
    if summary.get("m.joined_member_count") is not None:
        room_updates["member_count"] = int(summary.get("m.joined_member_count") or 0)

    for event in state.get("events", []):
        _apply_state_event(conn, room_id, event, own_user_id, changes)

    new_message_rows: list[dict] = []
    call_events: list[dict] = []
    for event in timeline.get("events", []):
        event_type = event.get("type", "")
        if isinstance(event_type, str) and event_type.startswith("m.call."):
            # VoIP signaling (HLD U25.19/U25.20): persisted raw + relayed via
            # SSE so the browser's WebRTC layer can act on it.
            call_events.append(_apply_call_event(conn, room_id, event))
            continue
        row = _apply_timeline_event(conn, own_user_id, room_id, event)
        if row:
            new_message_rows.append(row)

    if timeline.get("prev_batch"):
        current = cache_db.get_room(conn, room_id)
        if current and not current.get("prev_batch"):
            room_updates["prev_batch"] = timeline["prev_batch"]

    latest_ts = cache_db.latest_event_ts(conn, room_id)
    if latest_ts is not None:
        room_updates["last_event_ts"] = latest_ts
        room_updates["sort_ts"] = latest_ts
        preview = _latest_preview(conn, room_id)
        if preview is not None:
            room_updates["last_event_preview"] = preview

    if room_updates:
        cache_db.update_room_fields(conn, room_id, room_updates)

    typing_users = [
        user
        for event in ephemeral.get("events", [])
        if event.get("type") == "m.typing"
        for user in event.get("content", {}).get("user_ids", [])
        if user != own_user_id
    ]
    if typing_users:
        changes["typing"][room_id] = typing_users

    room_receipts = receipts.extract_read_receipts(ephemeral.get("events", []), own_user_id)
    for receipt in room_receipts:
        cache_db.upsert_receipt(
            conn,
            room_id,
            receipt["user_id"],
            receipts.RECEIPT_TYPE_READ,
            receipt["event_id"],
            receipt["ts"],
        )
    if room_receipts:
        changes["receipts"][room_id] = room_receipts

    fully_read = next(
        (
            event.get("content", {}).get("event_id")
            for event in account_data.get("events", [])
            if event.get("type") == "m.fully_read"
        ),
        None,
    )
    if fully_read:
        cache_db.set_read_state(conn, room_id, fully_read_event_id=fully_read)
        if not room_updates.get("notification_count") and unread.get("notification_count") is None:
            cache_db.update_room_fields(conn, room_id, {"notification_count": 0})

    if new_message_rows or not existed:
        changes["messages"][room_id] = [
            cache_db.message_to_api(row, reactions=None, own_user_id=own_user_id)
            for row in new_message_rows
        ]
    if call_events:
        changes["calls"][room_id] = call_events
    changes["rooms_upserted"].append(room_id)


def _apply_state_event(conn, room_id: str, event: dict, own_user_id: str, changes: dict) -> None:
    event_type = event.get("type", "")
    content = event.get("content", {})
    if event_type == "m.room.name":
        cache_db.update_room_fields(conn, room_id, {"name": content.get("name")})
    elif event_type == "m.room.topic":
        cache_db.update_room_fields(conn, room_id, {"topic": content.get("topic")})
    elif event_type == "m.room.join_rules":
        cache_db.update_room_fields(
            conn, room_id, {"is_public": 1 if content.get("join_rule") == "public" else 0}
        )
    elif event_type == "m.room.member":
        state_key = event.get("state_key", "")
        cache_db.upsert_member(
            conn,
            room_id,
            state_key,
            displayname=content.get("displayname"),
            avatar_url=content.get("avatar_url"),
            membership=content.get("membership", "leave"),
        )
        if state_key == own_user_id and content.get("is_direct"):
            cache_db.update_room_fields(conn, room_id, {"is_direct": 1})


def _apply_timeline_event(conn, own_user_id: str, room_id: str, event: dict) -> dict | None:
    event_type = event.get("type", "")
    content = event.get("content", {})
    event_id = event.get("event_id", "")
    sender = event.get("sender", "")
    ts = int(event.get("origin_server_ts", 0) or 0)

    if event_type == "m.room.redaction":
        target = event.get("redacts") or content.get("redacts")
        if target:
            target_row = cache_db.get_message(conn, target)
            if target_row and target_row["type"] == "m.reaction":
                cache_db.delete_message(conn, target)
            else:
                cache_db.mark_redacted(conn, target)
        return None

    if event_type in ("m.room.message", "m.reaction"):
        relates = content.get("m.relates_to") or {}
        rel_type = relates.get("rel_type")
        if rel_type == "m.replace":
            target = relates.get("event_id")
            new_content = content.get("m.new_content") or content
            if target:
                cache_db.update_message_content(
                    conn, target, json.dumps(new_content), new_content.get("body")
                )
            return None

        row_body = content.get("body")
        if event_type == "m.reaction":
            row_body = relates.get("key") or row_body
        row = {
            "event_id": event_id,
            "room_id": room_id,
            "sender": sender,
            "type": event_type,
            "body": row_body,
            "content_json": json.dumps(content),
            "relates_to": relates.get("event_id"),
            "rel_type": rel_type,
            "origin_server_ts": ts,
        }
        cache_db.insert_message(conn, **row)
        return row

    if event_type == "m.room.member":
        state_key = event.get("state_key", "")
        cache_db.upsert_member(
            conn,
            room_id,
            state_key,
            displayname=content.get("displayname"),
            avatar_url=content.get("avatar_url"),
            membership=content.get("membership", "leave"),
        )
        if state_key == own_user_id and content.get("is_direct"):
            cache_db.update_room_fields(conn, room_id, {"is_direct": 1})
    elif event_type == "m.room.name":
        cache_db.update_room_fields(conn, room_id, {"name": content.get("name")})
    elif event_type == "m.room.topic":
        cache_db.update_room_fields(conn, room_id, {"topic": content.get("topic")})
    return None


def _apply_call_event(conn, room_id: str, event: dict) -> dict:
    """Persist one m.call.* timeline event and return its SSE payload."""
    content = event.get("content", {})
    event_id = event.get("event_id", "")
    row = {
        "event_id": event_id,
        "room_id": room_id,
        "sender": event.get("sender", ""),
        "call_id": str(content.get("call_id", "")),
        "event_type": event.get("type", ""),
        "content_json": json.dumps(content),
        "origin_server_ts": int(event.get("origin_server_ts", 0) or 0),
    }
    cache_db.insert_call_event(conn, **row)
    return {
        "event_id": row["event_id"],
        "room_id": room_id,
        "sender": row["sender"],
        "type": row["event_type"],
        "call_id": row["call_id"],
        "content": content,
        "origin_server_ts": row["origin_server_ts"],
    }


def _latest_preview(conn, room_id: str) -> str | None:
    rows = conn.execute(
        """
        SELECT body, type, sender, redacted FROM chat_messages
        WHERE room_id = ? ORDER BY origin_server_ts DESC, event_id DESC LIMIT 1
        """,
        (room_id,),
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    if row["redacted"]:
        return "Message deleted"
    if row["type"] == "m.reaction":
        return f"Reacted {row['body'] or ''}".strip()
    return (row["body"] or "")[:80] or None


def process_backfill(conn, own_user_id: str, room_id: str, response: dict) -> list[dict]:
    """Apply a /rooms/<id>/messages (dir=b) response to the cache."""
    rows: list[dict] = []
    for event in response.get("chunk", []):
        event_type = event.get("type", "")
        if isinstance(event_type, str) and event_type.startswith("m.call."):
            # History: persist signaling so call timeline entries survive a
            # room reload, but do not return rows (callers expect messages).
            _apply_call_event(conn, room_id, event)
        elif event_type in ("m.room.message", "m.reaction"):
            row = _apply_timeline_event(conn, own_user_id, room_id, event)
            if row:
                rows.append(row)
        elif event_type == "m.room.member":
            _apply_timeline_event(conn, own_user_id, room_id, event)
    end = response.get("end")
    if end:
        cache_db.update_room_fields(conn, room_id, {"prev_batch": end})
    return rows
