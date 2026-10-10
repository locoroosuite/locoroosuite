"""U5.4a/U4A.13b: the per-user folders table is mirrored from the server's
IMAP LIST on every sync, and fatal sync errors name the requested folder
(UX7) so the folder view can surface them."""

import sqlite3

from app.modules.mail.services.imap_sync import _mirror_folder_list, _requested_folder_name


def _mirror_conn(folder_rows=()):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT UNIQUE NOT NULL, unread_count INTEGER DEFAULT 0)"
    )
    for name, unread in folder_rows:
        conn.execute("INSERT INTO folders (name, unread_count) VALUES (?, ?)", (name, unread))
    conn.commit()
    return conn


def _folder_rows(conn):
    return dict(conn.execute("SELECT name, unread_count FROM folders").fetchall())


class TestMirrorFolderList:
    def test_adds_newly_seen_folders(self):
        conn = _mirror_conn([("INBOX", 0)])
        _mirror_folder_list(conn, ["INBOX", "Junk", "Archive"])
        assert _folder_rows(conn) == {"INBOX": 0, "Junk": 0, "Archive": 0}

    def test_removes_stale_rows(self):
        conn = _mirror_conn([("INBOX", 0), ("OldName", 4)])
        _mirror_folder_list(conn, ["INBOX", "Renamed"])
        assert _folder_rows(conn) == {"INBOX": 0, "Renamed": 0}

    def test_preserves_unread_counts_of_kept_folders(self):
        conn = _mirror_conn([("INBOX", 5)])
        _mirror_folder_list(conn, ["INBOX", "Junk"])
        assert _folder_rows(conn) == {"INBOX": 5, "Junk": 0}

    def test_empty_list_is_a_noop(self):
        conn = _mirror_conn([("INBOX", 2)])
        _mirror_folder_list(conn, [])
        assert _folder_rows(conn) == {"INBOX": 2}

    def test_failure_is_swallowed_without_raising(self):
        conn = sqlite3.connect(":memory:")  # no folders table at all
        _mirror_folder_list(conn, ["INBOX"])  # must not raise
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone() is not None


class TestRequestedFolderName:
    def test_single_string_passthrough(self):
        assert _requested_folder_name("Trash") == "Trash"

    def test_list_uses_first_entry(self):
        assert _requested_folder_name(["Trash", "Junk"]) == "Trash"

    def test_none_defaults_to_inbox(self):
        assert _requested_folder_name(None) == "INBOX"

    def test_empty_defaults_to_inbox(self):
        assert _requested_folder_name("") == "INBOX"
        assert _requested_folder_name([]) == "INBOX"
