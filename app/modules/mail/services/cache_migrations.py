"""Versioned schema migrations for the mail cache database.

Each migration is self-guarding: it inspects the actual schema and no-ops if the
change is already applied. This makes the chain safe to run against fresh DBs,
pre-versioning DBs, and the corrupt-schema DB that motivated this work (a stale
``account_id NOT NULL`` column on ``folders``).

The runner (``app.shared.migrations.run_migrations``) records each applied
migration in ``_schema_migrations`` so subsequent opens skip the chain.

To add a new migration: append a ``Migration("NNNN_name", fn)`` to the
``MAIL_CACHE_MIGRATIONS`` tuple. The function must self-guard.
"""

from __future__ import annotations

import logging

import sqlcipher3

from app.shared.migrations import Migration, table_columns

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 0001 — Baseline schema (full current shape)
#
# Creates every table with its complete current column set, plus FTS, triggers,
# and indexes. For fresh DBs this is the only migration that does real work;
# 0002–0004 are no-ops. For pre-versioning DBs, CREATE TABLE IF NOT EXISTS
# leaves existing tables untouched and 0002–0004 fill the gaps.
# ---------------------------------------------------------------------------


def _baseline_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY,
            name TEXT,
            unread_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS folder_state (
            folder TEXT PRIMARY KEY,
            uidvalidity TEXT,
            uidnext INTEGER,
            highestmodseq TEXT,
            last_sync_at TEXT,
            last_new_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            recipients TEXT,
            date TEXT,
            date_ts INTEGER,
            internal_date_ts INTEGER,
            flags TEXT,
            snippet TEXT,
            body TEXT,
            body_html TEXT,
            has_attachments INTEGER DEFAULT 0,
            message_id TEXT,
            thread_id TEXT,
            in_reply_to TEXT,
            ref_list TEXT,
            calendar_event_uid TEXT,
            is_bounce INTEGER DEFAULT 0,
            bounce_reason TEXT,
            original_subject TEXT,
            cc TEXT,
            attachment_list TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_tags (
            message_id INTEGER,
            tag_id INTEGER,
            PRIMARY KEY (message_id, tag_id)
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
            subject, sender, recipients, body, content='messages', content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO message_fts(rowid, subject, sender, recipients, body)
            VALUES (new.id, new.subject, new.sender, new.recipients, new.body);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO message_fts(message_fts, rowid, subject, sender, recipients, body)
            VALUES ('delete', old.id, old.subject, old.sender, old.recipients, old.body);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO message_fts(message_fts, rowid, subject, sender, recipients, body)
            VALUES ('delete', old.id, old.subject, old.sender, old.recipients, old.body);
            INSERT INTO message_fts(rowid, subject, sender, recipients, body)
            VALUES (new.id, new.subject, new.sender, new.recipients, new.body);
        END;
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS messages_folder_date_ts_idx "
        "ON messages(folder, COALESCE(internal_date_ts, date_ts) DESC, id DESC)"
    )


# ---------------------------------------------------------------------------
# 0002 — Ensure historically-added message columns exist (old-DB upgrade path)
# ---------------------------------------------------------------------------


def _ensure_message_columns(conn) -> None:
    cols = table_columns(conn, "messages")

    if "date_ts" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN date_ts INTEGER")
        from app.modules.mail.services.cache_db import _date_to_unix

        rows = conn.execute("SELECT id, date FROM messages").fetchall()
        for message_id, date_value in rows:
            conn.execute(
                "UPDATE messages SET date_ts = ? WHERE id = ?",
                (_date_to_unix(date_value), message_id),
            )

    if "internal_date_ts" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN internal_date_ts INTEGER")

    if "in_reply_to" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN in_reply_to TEXT")
    if "ref_list" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN ref_list TEXT")
    if "thread_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN thread_id TEXT")

    if "calendar_event_uid" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN calendar_event_uid TEXT")

    if "is_bounce" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN is_bounce INTEGER DEFAULT 0")
    if "bounce_reason" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN bounce_reason TEXT")
    if "original_subject" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN original_subject TEXT")

    if "cc" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN cc TEXT")

    if "attachment_list" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN attachment_list TEXT")

    if "body_html" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN body_html TEXT")


# ---------------------------------------------------------------------------
# 0003 — Ensure folder columns + unique-name index (old-DB upgrade path)
#
# Legacy ``folders`` tables predate the ``unread_count`` column and/or the
# UNIQUE constraint on ``name``. Without uniqueness, ``upsert_folder``'s
# ``ON CONFLICT(name)`` fails and every folder sync errors out forever.
# ---------------------------------------------------------------------------


def _ensure_folder_columns(conn) -> None:
    cols = table_columns(conn, "folders")
    if "unread_count" not in cols:
        conn.execute("ALTER TABLE folders ADD COLUMN unread_count INTEGER DEFAULT 0")

    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS folders_name_idx ON folders(name)")
    except sqlcipher3.dbapi2.IntegrityError:
        conn.execute(
            """
            DELETE FROM folders
            WHERE id NOT IN (
                SELECT MIN(id) FROM folders GROUP BY name
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS folders_name_idx ON folders(name)")


# ---------------------------------------------------------------------------
# 0004 — Ensure message indexes (old-DB upgrade path)
#
# The uid+folder unique index may need dedup before it can be created.
# The folder/date index is created in the baseline; this migration is a
# safety net for DBs that predate the baseline.
# ---------------------------------------------------------------------------


def _ensure_message_indexes(conn) -> None:
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS messages_uid_folder_idx ON messages(uid, folder)"
        )
    except sqlcipher3.dbapi2.IntegrityError:
        conn.execute(
            """
            DELETE FROM messages
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM messages
                WHERE uid IS NOT NULL AND folder IS NOT NULL
                GROUP BY uid, folder
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS messages_uid_folder_idx ON messages(uid, folder)"
        )


# ---------------------------------------------------------------------------
# 0005 — Drop the stale ``account_id`` column from ``folders``
#
# A previous schema version created ``folders`` with an ``account_id NOT NULL``
# column. The current code never populates it (folders are scoped by cache DB
# file, one per account), so every ``upsert_folder`` insert fails with
# ``IntegrityError: NOT NULL constraint failed: folders.account_id``. This
# migration rebuilds the table via the SQLite table-rebuild dance to remove the
# rogue column while preserving id/name/unread_count data.
# ---------------------------------------------------------------------------


def _drop_folders_account_id(conn) -> None:
    cols = table_columns(conn, "folders")
    if "account_id" not in cols:
        return

    _logger.info("rebuilding folders table to drop stale account_id column")
    conn.execute(
        """
        CREATE TABLE _folders_migration (
            id INTEGER PRIMARY KEY,
            name TEXT,
            unread_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO _folders_migration(id, name, unread_count)
        SELECT id, name, unread_count FROM folders
        """
    )
    conn.execute("DROP TABLE folders")
    conn.execute("ALTER TABLE _folders_migration RENAME TO folders")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS folders_name_idx ON folders(name)")


MAIL_CACHE_MIGRATIONS: tuple[Migration, ...] = (
    Migration("mail_0001_baseline_schema", _baseline_schema),
    Migration("mail_0002_ensure_message_columns", _ensure_message_columns),
    Migration("mail_0003_ensure_folder_columns", _ensure_folder_columns),
    Migration("mail_0004_ensure_message_indexes", _ensure_message_indexes),
    Migration("mail_0005_drop_folders_account_id", _drop_folders_account_id),
)
