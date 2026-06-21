import json
import os

import sqlcipher3

from app.modules.mail.controllers.helpers import _decorate_message_row
from app.modules.mail.services.cache_db import (
    create_tag,
    get_message,
    init_cache_schema,
    list_flagged,
    list_messages_by_tag,
    list_messages_for_folder_view,
    list_unread,
    list_with_attachments,
    open_cache,
    search_local,
    tag_message,
    upsert_message,
)

_VIEW_COLUMNS = {
    "id", "subject", "sender", "snippet", "date", "flags", "body", "folder",
    "thread_id", "recipients", "sort_ts", "is_bounce", "bounce_reason",
    "original_subject", "has_attachments",
}


def _make_cache(tmp_path):
    db_path = str(tmp_path / "mail.db")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
    init_cache_schema(conn)
    return conn, db_path


def _seed_message(
    conn,
    uid="1",
    folder="INBOX",
    subject="Test Subject",
    sender="a@b.com",
    recipients="c@d.com",
    date="Mon, 1 Jan 2024 10:00:00 +0000",
    flags=None,
    snippet="snip",
    body="body",
    has_attachments=False,
    message_id="<msg1@test.com>",
    thread_id="thread-abc",
    cc=None,
):
    upsert_message(
        conn, uid, folder, subject, sender, recipients, date, flags or [], snippet, body,
        has_attachments, message_id, thread_id=thread_id, cc=cc,
    )
    return get_message(conn, 1)


def test_open_cache_reinitializes_after_external_deletion(tmp_path):
    """Regression: an in-process memo marks cache paths as schema-initialized.
    If a cache file is deleted externally (cache reset / E2E cleanup) and the
    path is reused, open_cache must re-run init_cache_schema instead of trusting
    the stale memo and returning a schema-less connection."""
    key = "0" * 64
    db_path = str(tmp_path / "mail.db")
    conn = open_cache(db_path, key)
    conn.execute("INSERT INTO folders (name, unread_count) VALUES ('INBOX', 1)")
    conn.close()
    assert db_path  # memoized now in _SCHEMA_INITIALIZED

    # External deletion invalidates the memoized path.
    os.unlink(db_path)
    assert not os.path.exists(db_path)

    conn = open_cache(db_path, key)
    # Schema must be present even though the path was already memoized.
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "messages" in tables
    assert "folders" in tables
    # And the previously-inserted row is gone (fresh file).
    assert conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0
    conn.close()


def test_get_message_columns(tmp_path):
    conn, _ = _make_cache(tmp_path)
    row = _seed_message(conn, thread_id="thread-xyz", has_attachments=True, cc="e@f.com")
    assert set(row.keys()) == {
        "id", "uid", "folder", "subject", "sender", "recipients", "date", "flags",
        "snippet", "body", "body_html", "has_attachments", "message_id", "thread_id", "cc",
    }
    assert row["thread_id"] == "thread-xyz"
    assert row["has_attachments"] == 1
    assert row["cc"] == "e@f.com"
    assert row["folder"] == "INBOX"
    assert row["uid"] == "1"


def test_get_message_thread_id_distinct_from_has_attachments(tmp_path):
    conn, _ = _make_cache(tmp_path)
    row = _seed_message(conn, thread_id="real-thread", has_attachments=True)
    assert row["thread_id"] == "real-thread"
    assert bool(row["has_attachments"]) is True
    assert row["thread_id"] != row["has_attachments"]


def test_search_local_columns(tmp_path):
    conn, _ = _make_cache(tmp_path)
    _seed_message(conn, subject="Quarterly Report", snippet="numbers", body="numbers")
    rows = search_local(conn, "Quarterly")
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == {
        "id", "uid", "folder", "subject", "sender", "recipients", "date", "flags",
        "body", "has_attachments", "message_id", "thread_id", "snippet",
    }
    assert row["subject"] == "Quarterly Report"
    assert row["snippet"] == "numbers"


def test_list_flagged_filters_by_flag(tmp_path):
    conn, _ = _make_cache(tmp_path)
    _seed_message(conn, uid="1", subject="Flagged", flags=["\\Flagged", "\\Seen"])
    _seed_message(conn, uid="2", subject="Plain", flags=["\\Seen"])
    rows = list_flagged(conn)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == _VIEW_COLUMNS
    assert row["subject"] == "Flagged"
    assert "\\Flagged" in json.loads(row["flags"])
    assert _decorate_message_row(row, timezone_name="UTC")["folder"] == "INBOX"


def test_list_unread_columns(tmp_path):
    conn, _ = _make_cache(tmp_path)
    _seed_message(conn, uid="1", subject="Unread One", flags=[])
    _seed_message(conn, uid="2", subject="Read Two", flags=["\\Seen"])
    rows = list_unread(conn)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == _VIEW_COLUMNS
    assert row["subject"] == "Unread One"
    assert _decorate_message_row(row, timezone_name="UTC")["thread_id"] is not None


def test_list_with_attachments_columns(tmp_path):
    conn, _ = _make_cache(tmp_path)
    _seed_message(conn, uid="1", subject="With File", has_attachments=True)
    _seed_message(conn, uid="2", subject="No File", has_attachments=False)
    rows = list_with_attachments(conn)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == _VIEW_COLUMNS
    assert row["subject"] == "With File"
    assert _decorate_message_row(row, timezone_name="UTC")["has_attachments"] is True


def test_list_messages_by_tag_columns(tmp_path):
    conn, _ = _make_cache(tmp_path)
    _seed_message(conn, uid="1", subject="Tagged", thread_id="t-tag")
    create_tag(conn, "Important")
    tag_message(conn, 1, 1)
    rows = list_messages_by_tag(conn, 1)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == _VIEW_COLUMNS
    assert row["subject"] == "Tagged"
    assert _decorate_message_row(row, timezone_name="UTC")["thread_id"] == "t-tag"


def test_list_messages_for_folder_view_columns(tmp_path):
    conn, _ = _make_cache(tmp_path)
    _seed_message(conn, subject="Folder Msg", thread_id="t1")
    rows = list_messages_for_folder_view(conn, "INBOX")
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == {
        "id", "subject", "sender", "snippet", "date", "flags", "body", "folder",
        "thread_id", "recipients", "sort_ts", "is_bounce", "bounce_reason",
        "original_subject", "has_attachments",
    }
    assert row["thread_id"] == "t1"
    assert row["folder"] == "INBOX"
    assert row["subject"] == "Folder Msg"


def test_decorate_message_row_shape(tmp_path):
    conn, _ = _make_cache(tmp_path)
    upsert_message(
        conn, "1", "INBOX", "Re: Hello", "Alice <a@b.com>", "c@d.com",
        "Mon, 1 Jan 2024 10:00:00 +0000", ["\\Seen", "\\Flagged"], "preview", "body",
        True, "<msg1@test.com>", thread_id="th-1", is_bounce=True,
        bounce_reason="550 denied", original_subject="Hello",
    )
    row = list_messages_for_folder_view(conn, "INBOX")[0]
    decorated = _decorate_message_row(row, timezone_name="UTC", is_sent=False)
    assert set(decorated.keys()) == {
        "id", "subject", "sender", "sender_display", "sender_tooltip", "snippet",
        "date", "date_ts", "sort_ts", "date_display", "flags", "is_unread",
        "is_flagged", "folder", "thread_id", "is_sent", "is_draft",
        "recipients_display", "is_bounce", "bounce_reason", "has_attachments",
    }
    assert decorated["folder"] == "INBOX"
    assert decorated["thread_id"] == "th-1"
    assert decorated["is_unread"] is False
    assert decorated["is_flagged"] is True
    assert decorated["is_bounce"] is True
    assert decorated["bounce_reason"] == "550 denied"
    assert decorated["has_attachments"] is True
    assert decorated["subject"] == "Hello"
