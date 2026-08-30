from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlcipher3

from app.modules.chat.services.cache_migrations import CHAT_CACHE_MIGRATIONS
from app.shared.cache_errors import CacheKeyMismatchError
from app.shared.migrations import run_migrations

ROOM_COLS = (
    "room_id",
    "name",
    "topic",
    "avatar_url",
    "is_direct",
    "is_public",
    "membership",
    "notification_count",
    "highlight_count",
    "member_count",
    "prev_batch",
    "last_event_ts",
    "last_event_preview",
    "sort_ts",
    "updated_at",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def open_cache(db_path, key):
    if not key:
        raise ValueError("cache key required")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_migrations(conn, CHAT_CACHE_MIGRATIONS)
    except Exception as exc:
        conn.close()
        import os as _os

        if _os.path.exists(db_path):
            _os.unlink(db_path)
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            run_migrations(conn, CHAT_CACHE_MIGRATIONS)
        except Exception:
            conn.close()
            raise CacheKeyMismatchError(
                f"Failed to open chat cache database even after reset. db_path={db_path}"
            ) from exc
    return conn


def _row_to_dict(row) -> dict | None:
    return dict(zip(row.keys(), row, strict=False)) if row else None


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(zip(r.keys(), r, strict=False)) for r in rows]


# --- credentials -------------------------------------------------------


def get_credentials(conn) -> dict | None:
    row = conn.execute("SELECT * FROM chat_credentials WHERE id = 1").fetchone()
    return _row_to_dict(row)


def set_credentials(
    conn,
    *,
    matrix_user_id: str,
    access_token: str,
    device_id: str | None,
    password_encrypted: str,
    homeserver_url: str,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO chat_credentials
            (id, matrix_user_id, access_token, device_id, password_encrypted, homeserver_url, created_at, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            matrix_user_id = excluded.matrix_user_id,
            access_token = excluded.access_token,
            device_id = excluded.device_id,
            password_encrypted = excluded.password_encrypted,
            homeserver_url = excluded.homeserver_url,
            updated_at = excluded.updated_at
        """,
        (matrix_user_id, access_token, device_id, password_encrypted, homeserver_url, now, now),
    )
    conn.commit()


# --- sync state --------------------------------------------------------


def get_sync_state(conn) -> dict | None:
    row = conn.execute("SELECT since_token, synced_at FROM chat_sync_state WHERE id = 1").fetchone()
    return _row_to_dict(row)


def set_sync_state(conn, since_token: str | None) -> None:
    conn.execute(
        """
        INSERT INTO chat_sync_state (id, since_token, synced_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET since_token = excluded.since_token, synced_at = excluded.synced_at
        """,
        (since_token, _now()),
    )
    conn.commit()


# --- rooms -------------------------------------------------------------


def upsert_room(
    conn,
    room_id: str,
    *,
    name: str | None = None,
    topic: str | None = None,
    is_direct: bool = False,
    is_public: bool = False,
    membership: str = "join",
    prev_batch: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO chat_rooms
            (room_id, name, topic, is_direct, is_public, membership, prev_batch, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(room_id) DO UPDATE SET
            name = COALESCE(excluded.name, chat_rooms.name),
            topic = excluded.topic,
            is_direct = MAX(chat_rooms.is_direct, excluded.is_direct),
            is_public = excluded.is_public,
            membership = excluded.membership,
            prev_batch = COALESCE(excluded.prev_batch, chat_rooms.prev_batch),
            updated_at = excluded.updated_at
        """,
        (room_id, name, topic, int(is_direct), int(is_public), membership, prev_batch, now),
    )
    conn.commit()


def update_room_fields(conn, room_id: str, updates: dict) -> None:
    if not updates:
        return
    allowed = {
        "name",
        "topic",
        "avatar_url",
        "is_direct",
        "is_public",
        "membership",
        "notification_count",
        "highlight_count",
        "member_count",
        "prev_batch",
        "last_event_ts",
        "last_event_preview",
        "sort_ts",
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE chat_rooms SET {set_clause} WHERE room_id = ?",
        (*fields.values(), room_id),
    )
    conn.commit()


def get_room(conn, room_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM chat_rooms WHERE room_id = ?", (room_id,)).fetchone()
    return _row_to_dict(row)


def delete_room(conn, room_id: str) -> None:
    conn.execute("DELETE FROM chat_messages WHERE room_id = ?", (room_id,))
    conn.execute("DELETE FROM chat_room_members WHERE room_id = ?", (room_id,))
    conn.execute("DELETE FROM chat_read_state WHERE room_id = ?", (room_id,))
    conn.execute("DELETE FROM chat_rooms WHERE room_id = ?", (room_id,))
    conn.commit()


def list_rooms(conn, own_user_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM chat_rooms
        WHERE membership IN ('join', 'invite')
        ORDER BY COALESCE(sort_ts, last_event_ts, 0) DESC, updated_at DESC
        """
    ).fetchall()
    rooms = _rows_to_dicts(rows)
    for room in rooms:
        _decorate_room_display_name(conn, room, own_user_id)
    return rooms


def _decorate_room_display_name(conn, room: dict, own_user_id: str) -> None:
    if room.get("name"):
        room["display_name"] = room["name"]
    elif room.get("is_direct"):
        row = conn.execute(
            """
            SELECT displayname, user_id FROM chat_room_members
            WHERE room_id = ? AND membership = 'join' AND user_id != ?
            ORDER BY displayname LIMIT 1
            """,
            (room["room_id"], own_user_id),
        ).fetchone()
        if row:
            room["display_name"] = row["displayname"] or _localpart(row["user_id"])
        else:
            room["display_name"] = "Direct message"
    else:
        room["display_name"] = "Unnamed room"


def _localpart(user_id: str) -> str:
    return user_id[1:].split(":", 1)[0] if user_id.startswith("@") else user_id


# --- members -----------------------------------------------------------


def upsert_member(
    conn,
    room_id: str,
    user_id: str,
    *,
    displayname: str | None = None,
    avatar_url: str | None = None,
    membership: str = "join",
    power_level: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_room_members (room_id, user_id, displayname, avatar_url, membership, power_level)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(room_id, user_id) DO UPDATE SET
            displayname = COALESCE(excluded.displayname, chat_room_members.displayname),
            avatar_url = COALESCE(excluded.avatar_url, chat_room_members.avatar_url),
            membership = excluded.membership,
            power_level = excluded.power_level
        """,
        (room_id, user_id, displayname, avatar_url, membership, power_level),
    )
    conn.commit()


def update_member_membership(conn, room_id: str, user_id: str, membership: str) -> None:
    conn.execute(
        "UPDATE chat_room_members SET membership = ? WHERE room_id = ? AND user_id = ?",
        (membership, room_id, user_id),
    )
    conn.commit()


def list_members(conn, room_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM chat_room_members WHERE room_id = ? ORDER BY displayname, user_id",
        (room_id,),
    ).fetchall()
    return _rows_to_dicts(rows)


def count_joined_members(conn, room_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM chat_room_members WHERE room_id = ? AND membership = 'join'",
        (room_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


# --- messages ----------------------------------------------------------


def insert_message(
    conn,
    *,
    event_id: str,
    room_id: str,
    sender: str,
    type: str,
    body: str | None,
    content_json: str,
    relates_to: str | None = None,
    rel_type: str | None = None,
    origin_server_ts: int,
    txn_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO chat_messages
            (event_id, room_id, sender, type, body, content_json, relates_to, rel_type,
             origin_server_ts, redacted, edited, txn_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        """,
        (
            event_id,
            room_id,
            sender,
            type,
            body,
            content_json,
            relates_to,
            rel_type,
            origin_server_ts,
            txn_id,
        ),
    )
    conn.commit()


def get_message(conn, event_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM chat_messages WHERE event_id = ?", (event_id,)).fetchone()
    return _row_to_dict(row)


def update_message_content(conn, event_id: str, content_json: str, body: str | None) -> None:
    conn.execute(
        "UPDATE chat_messages SET content_json = ?, body = ?, edited = 1 WHERE event_id = ?",
        (content_json, body, event_id),
    )
    conn.commit()


def mark_redacted(conn, event_id: str) -> None:
    conn.execute(
        "UPDATE chat_messages SET redacted = 1, body = NULL, content_json = '{}' WHERE event_id = ?",
        (event_id,),
    )
    conn.commit()


def delete_message(conn, event_id: str) -> None:
    conn.execute("DELETE FROM chat_messages WHERE event_id = ?", (event_id,))
    conn.commit()


def list_messages(conn, room_id: str, limit: int = 50, before_ts: int | None = None) -> list[dict]:
    if before_ts is not None:
        rows = conn.execute(
            """
            SELECT * FROM chat_messages
            WHERE room_id = ? AND origin_server_ts < ? AND type != 'm.reaction'
            ORDER BY origin_server_ts DESC, event_id DESC
            LIMIT ?
            """,
            (room_id, before_ts, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM chat_messages
            WHERE room_id = ? AND type != 'm.reaction'
            ORDER BY origin_server_ts DESC, event_id DESC
            LIMIT ?
            """,
            (room_id, limit),
        ).fetchall()
    return _rows_to_dicts(reversed(rows))


def list_reactions(conn, room_id: str) -> dict[str, list[dict]]:
    rows = conn.execute(
        """
        SELECT relates_to, body, sender, redacted FROM chat_messages
        WHERE room_id = ? AND type = 'm.reaction'
        ORDER BY origin_server_ts
        """,
        (room_id,),
    ).fetchall()
    result: dict[str, list[dict]] = {}
    for row in rows:
        if row["redacted"] or not row["relates_to"]:
            continue
        result.setdefault(row["relates_to"], []).append(
            {"key": row["body"], "sender": row["sender"]}
        )
    return result


def latest_event_ts(conn, room_id: str) -> int | None:
    row = conn.execute(
        "SELECT MAX(origin_server_ts) AS m FROM chat_messages WHERE room_id = ?",
        (room_id,),
    ).fetchone()
    return int(row["m"]) if row and row["m"] is not None else None


# --- read state --------------------------------------------------------


def set_read_state(
    conn,
    room_id: str,
    *,
    read_event_id: str | None = None,
    read_ts: int | None = None,
    fully_read_event_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_read_state (room_id, read_event_id, read_ts, fully_read_event_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(room_id) DO UPDATE SET
            read_event_id = COALESCE(excluded.read_event_id, chat_read_state.read_event_id),
            read_ts = COALESCE(excluded.read_ts, chat_read_state.read_ts),
            fully_read_event_id = COALESCE(excluded.fully_read_event_id, chat_read_state.fully_read_event_id)
        """,
        (room_id, read_event_id, read_ts, fully_read_event_id),
    )
    conn.commit()


def get_read_state(conn, room_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM chat_read_state WHERE room_id = ?", (room_id,)).fetchone()
    return _row_to_dict(row)


def message_to_api(
    row: dict, reactions: list[dict] | None = None, own_user_id: str | None = None
) -> dict:
    try:
        content = json.loads(row["content_json"])
    except (ValueError, TypeError):
        content = {}
    return {
        "event_id": row["event_id"],
        "room_id": row["room_id"],
        "sender": row["sender"],
        "type": row["type"],
        "body": row.get("body"),
        "content": content,
        "origin_server_ts": row["origin_server_ts"],
        "redacted": bool(row.get("redacted", 0)),
        "edited": bool(row.get("edited", 0)),
        "reactions": _aggregate_reactions(reactions or [], own_user_id),
    }


def _aggregate_reactions(reactions: list[dict], own_user_id: str | None) -> list[dict]:
    grouped: dict[str, dict] = {}
    for reaction in reactions:
        key = reaction.get("key") or "?"
        entry = grouped.setdefault(key, {"key": key, "count": 0, "mine": False})
        entry["count"] += 1
        if own_user_id and reaction.get("sender") == own_user_id:
            entry["mine"] = True
    return list(grouped.values())
