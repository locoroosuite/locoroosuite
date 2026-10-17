"""Shared helpers for mail controller tests.

`cache_conn_factory` builds a real sqlite3 connection with the cache-schema
subset consumed by ``move_destination_folders`` (U5.5a), so tests exercise
the actual folder-sourcing SQL instead of mocking it away — the search
bulk-move bug (empty custom folders hidden) survived precisely because
pickers were tested against canned mock rows.
"""

import sqlite3

import pytest


@pytest.fixture()
def cache_conn_factory():
    def make(folders=(), message_dates=()):
        """Seed an in-memory cache conn.

        folders: raw IMAP folder names inserted into the ``folders`` table
        (the U5.4a IMAP LIST mirror).
        message_dates: (folder, internal_date_ts) tuples inserted into
        ``messages`` to drive the recent-activity ordering.
        """
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Cache-schema subset used by list_cached_folders/_latest_message_dates:
        # folders(name, unread_count); messages(folder, date, internal_date_ts, date_ts).
        conn.execute(
            "CREATE TABLE folders (name TEXT PRIMARY KEY, unread_count INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE messages ("
            "folder TEXT, date TEXT, internal_date_ts INTEGER, date_ts INTEGER)"
        )
        for name in folders:
            conn.execute("INSERT INTO folders(name, unread_count) VALUES (?, 0)", (name,))
        for folder, ts in message_dates:
            conn.execute(
                "INSERT INTO messages(folder, internal_date_ts) VALUES (?, ?)",
                (folder, ts),
            )
        return conn

    return make
