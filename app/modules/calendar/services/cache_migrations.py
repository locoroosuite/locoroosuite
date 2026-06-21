"""Versioned schema migrations for the calendar cache database.

Previously the calendar cache used a ``try/except: pass`` pattern around
``ALTER TABLE`` statements (lines 144–155 of the old ``cache_db.py``), which
silently swallowed errors and violated the project's error-handling rules.
This replaces that pattern with proper self-guarding migrations.
"""

from __future__ import annotations

from app.shared.migrations import Migration, table_columns


def _baseline_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendars (
            id INTEGER PRIMARY KEY,
            uid TEXT UNIQUE NOT NULL,
            href TEXT UNIQUE NOT NULL,
            displayname TEXT NOT NULL DEFAULT '',
            color TEXT DEFAULT '#4285f4',
            description TEXT,
            is_visible INTEGER DEFAULT 1,
            is_default INTEGER DEFAULT 0,
            order_index INTEGER DEFAULT 0,
            last_sync_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY,
            uid TEXT NOT NULL,
            href TEXT,
            etag TEXT,
            calendar_id INTEGER NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            description TEXT,
            location TEXT,
            dtstart TEXT NOT NULL,
            dtend TEXT,
            all_day INTEGER DEFAULT 0,
            rrule TEXT,
            exdates TEXT,
            rdates TEXT,
            recurrence_id TEXT,
            organizer TEXT,
            attendees TEXT,
            status TEXT DEFAULT 'CONFIRMED',
            categories TEXT,
            class TEXT DEFAULT 'PUBLIC',
            url TEXT,
            timezone TEXT,
            sequence INTEGER DEFAULT 0,
            raw_ical TEXT NOT NULL,
            created_at TEXT,
            last_modified TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (calendar_id) REFERENCES calendars(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_reminders (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            trigger_val TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'DISPLAY',
            description TEXT,
            FOREIGN KEY (event_id) REFERENCES calendar_events(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_state (
            calendar_href TEXT PRIMARY KEY,
            sync_token TEXT,
            ctag TEXT,
            last_sync_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS calendar_events_fts USING fts5(
            summary, description, location,
            content='calendar_events', content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS cal_events_ai AFTER INSERT ON calendar_events BEGIN
            INSERT INTO calendar_events_fts(rowid, summary, description, location)
            VALUES (new.id, new.summary, new.description, new.location);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS cal_events_ad AFTER DELETE ON calendar_events BEGIN
            INSERT INTO calendar_events_fts(calendar_events_fts, rowid, summary, description, location)
            VALUES ('delete', old.id, old.summary, old.description, old.location);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS cal_events_au AFTER UPDATE ON calendar_events BEGIN
            INSERT INTO calendar_events_fts(calendar_events_fts, rowid, summary, description, location)
            VALUES ('delete', old.id, old.summary, old.description, old.location);
            INSERT INTO calendar_events_fts(rowid, summary, description, location)
            VALUES (new.id, new.summary, new.description, new.location);
        END
        """
    )


def _ensure_event_link_columns(conn) -> None:
    """Add email-source link columns to calendar_events (old-DB upgrade path).

    Previously done with bare ``try/except: pass`` which silently swallowed
    errors. Now self-guarded with proper column-existence checks.
    """
    cols = table_columns(conn, "calendar_events")
    if "timezone" not in cols:
        conn.execute("ALTER TABLE calendar_events ADD COLUMN timezone TEXT")
    if "source_email_message_id" not in cols:
        conn.execute("ALTER TABLE calendar_events ADD COLUMN source_email_message_id INTEGER")
    if "source_email_account_id" not in cols:
        conn.execute("ALTER TABLE calendar_events ADD COLUMN source_email_account_id INTEGER")


CALENDAR_CACHE_MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001_baseline_schema", _baseline_schema),
    Migration("0002_ensure_event_link_columns", _ensure_event_link_columns),
)
