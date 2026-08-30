from __future__ import annotations

from app.shared.migrations import Migration, has_table


def _baseline_schema(conn) -> None:
    if has_table(conn, "chat_credentials"):
        return
    conn.executescript(
        """
        CREATE TABLE chat_credentials (
            id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
            matrix_user_id TEXT NOT NULL,
            access_token TEXT NOT NULL,
            device_id TEXT,
            password_encrypted TEXT NOT NULL,
            homeserver_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE chat_rooms (
            room_id TEXT NOT NULL PRIMARY KEY,
            name TEXT,
            topic TEXT,
            avatar_url TEXT,
            is_direct INTEGER NOT NULL DEFAULT 0,
            is_public INTEGER NOT NULL DEFAULT 0,
            membership TEXT NOT NULL DEFAULT 'join',
            notification_count INTEGER NOT NULL DEFAULT 0,
            highlight_count INTEGER NOT NULL DEFAULT 0,
            member_count INTEGER NOT NULL DEFAULT 0,
            prev_batch TEXT,
            last_event_ts INTEGER,
            last_event_preview TEXT,
            sort_ts INTEGER,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE chat_room_members (
            room_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            displayname TEXT,
            avatar_url TEXT,
            membership TEXT NOT NULL,
            power_level INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (room_id, user_id)
        );

        CREATE TABLE chat_messages (
            event_id TEXT NOT NULL PRIMARY KEY,
            room_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            type TEXT NOT NULL,
            body TEXT,
            content_json TEXT NOT NULL,
            relates_to TEXT,
            rel_type TEXT,
            origin_server_ts INTEGER NOT NULL,
            redacted INTEGER NOT NULL DEFAULT 0,
            edited INTEGER NOT NULL DEFAULT 0,
            txn_id TEXT
        );
        CREATE INDEX idx_chat_messages_room_ts ON chat_messages(room_id, origin_server_ts);

        CREATE TABLE chat_read_state (
            room_id TEXT NOT NULL PRIMARY KEY,
            read_event_id TEXT,
            read_ts INTEGER,
            fully_read_event_id TEXT
        );

        CREATE TABLE chat_sync_state (
            id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
            since_token TEXT,
            synced_at TEXT
        );
        """
    )


CHAT_CACHE_MIGRATIONS: tuple[Migration, ...] = (
    Migration("chat_0001_baseline_schema", _baseline_schema),
)
