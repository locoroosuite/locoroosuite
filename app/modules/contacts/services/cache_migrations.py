"""Versioned schema migrations for the contacts cache database.

Previously the contacts cache had no migration capability at all — only
``CREATE TABLE IF NOT EXISTS``. This baseline migration captures the current
schema; future schema changes append new ``Migration`` entries to
``CONTACTS_CACHE_MIGRATIONS``.
"""

from __future__ import annotations

from app.shared.migrations import Migration


def _baseline_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY,
            uid TEXT UNIQUE NOT NULL,
            href TEXT UNIQUE,
            etag TEXT,
            fn TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            email_work TEXT,
            email_home TEXT,
            tel_work TEXT,
            tel_home TEXT,
            tel_cell TEXT,
            org TEXT,
            title TEXT,
            note TEXT,
            raw_vcard TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS addressbook_state (
            href TEXT PRIMARY KEY,
            sync_token TEXT,
            last_sync_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS contacts_fts USING fts5(
            fn, email_work, email_home, tel_work, tel_home, tel_cell,
            content='contacts', content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS contacts_ai AFTER INSERT ON contacts BEGIN
            INSERT INTO contacts_fts(rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES (new.id, new.fn, new.email_work, new.email_home, new.tel_work, new.tel_home, new.tel_cell);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS contacts_ad AFTER DELETE ON contacts BEGIN
            INSERT INTO contacts_fts(contacts_fts, rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES ('delete', old.id, old.fn, old.email_work, old.email_home, old.tel_work, old.tel_home, old.tel_cell);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS contacts_au AFTER UPDATE ON contacts BEGIN
            INSERT INTO contacts_fts(contacts_fts, rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES ('delete', old.id, old.fn, old.email_work, old.email_home, old.tel_work, old.tel_home, old.tel_cell);
            INSERT INTO contacts_fts(rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES (new.id, new.fn, new.email_work, new.email_home, new.tel_work, new.tel_home, new.tel_cell);
        END
        """
    )


CONTACTS_CACHE_MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001_baseline_schema", _baseline_schema),
)
