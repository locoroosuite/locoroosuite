from datetime import datetime, timezone

import sqlcipher3

from app.modules.mail.services.cache_db import (
    _date_to_unix,
    init_cache_schema,
    list_messages_with_threading,
    upsert_message,
)
from app.modules.mail.services.imap_client import _parse_fetch_item


class TestParseFetchItemInternalDate:
    def test_extracts_internal_date(self):
        meta = b'1 (FLAGS (\\Seen) INTERNALDATE "14-May-2026 10:25:02 +0000" UID 42)'
        raw = b"test body"
        uid, flags, body, internal_date = _parse_fetch_item((meta, raw))
        assert uid == "42"
        assert internal_date is not None
        assert internal_date.year == 2026
        assert internal_date.month == 5
        assert internal_date.day == 14

    def test_no_internal_date_returns_none(self):
        meta = b"1 (FLAGS (\\Seen) UID 42)"
        raw = b"test body"
        uid, flags, body, internal_date = _parse_fetch_item((meta, raw))
        assert internal_date is None

    def test_malformed_internal_date_returns_none(self):
        meta = b'1 (FLAGS (\\Seen) INTERNALDATE "not-a-date" UID 42)'
        raw = b"test body"
        uid, flags, body, internal_date = _parse_fetch_item((meta, raw))
        assert internal_date is None

    def test_non_tuple_item_no_internal_date(self):
        meta = b"1 (FLAGS (\\Seen) UID 42)"
        uid, flags, body, internal_date = _parse_fetch_item(meta)
        assert internal_date is None


class TestUpsertMessageInternalDateFallback:
    def _open_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)
        return conn

    def test_stores_both_date_ts_and_internal_date_ts(self, tmp_path):
        conn = self._open_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Test", "a@b.com", "c@d.com",
            "Mon, 13 May 2024 10:00:00 +0000", [], "s", "b", False,
            "<m1@t.com>",
            internal_date=datetime(2026, 5, 14, 10, 25, 2, tzinfo=timezone.utc),
        )
        rows = list_messages_with_threading(conn, "INBOX")
        assert len(rows) == 1
        date_ts, internal_date_ts = conn.execute(
            "SELECT date_ts, internal_date_ts FROM messages WHERE uid='1'"
        ).fetchone()
        assert date_ts == _date_to_unix("Mon, 13 May 2024 10:00:00 +0000")
        assert internal_date_ts == int(
            datetime(2026, 5, 14, 10, 25, 2, tzinfo=timezone.utc).timestamp()
        )

    def test_internal_date_stored_when_date_header_missing(self, tmp_path):
        conn = self._open_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Test", "a@b.com", "c@d.com",
            "", [], "s", "b", False, "<m1@t.com>",
            internal_date=datetime(2026, 5, 14, 10, 25, 2, tzinfo=timezone.utc),
        )
        rows = list_messages_with_threading(conn, "INBOX")
        assert len(rows) == 1
        date_ts, internal_date_ts = conn.execute(
            "SELECT date_ts, internal_date_ts FROM messages WHERE uid='1'"
        ).fetchone()
        assert date_ts is None
        expected = int(datetime(2026, 5, 14, 10, 25, 2, tzinfo=timezone.utc).timestamp())
        assert internal_date_ts == expected

    def test_internal_date_stored_when_date_header_invalid(self, tmp_path):
        conn = self._open_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Test", "a@b.com", "c@d.com",
            "not-a-valid-date", [], "s", "b", False, "<m1@t.com>",
            internal_date=datetime(2026, 5, 14, 10, 25, 2, tzinfo=timezone.utc),
        )
        date_ts, internal_date_ts = conn.execute(
            "SELECT date_ts, internal_date_ts FROM messages WHERE uid='1'"
        ).fetchone()
        assert date_ts is None
        expected = int(datetime(2026, 5, 14, 10, 25, 2, tzinfo=timezone.utc).timestamp())
        assert internal_date_ts == expected

    def test_no_internal_date_and_no_date_header_gives_both_null(self, tmp_path):
        conn = self._open_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Test", "a@b.com", "c@d.com",
            "", [], "s", "b", False, "<m1@t.com>",
        )
        date_ts, internal_date_ts = conn.execute(
            "SELECT date_ts, internal_date_ts FROM messages WHERE uid='1'"
        ).fetchone()
        assert date_ts is None
        assert internal_date_ts is None

    def test_sort_order_with_internal_date_fallback(self, tmp_path):
        conn = self._open_db(tmp_path)
        upsert_message(
            conn, "2", "INBOX", "Old date header", "a@b.com", "c@d.com",
            "Mon, 13 May 2024 08:56:08 +0000", [], "s", "b", False,
            "<m2@t.com>",
        )
        upsert_message(
            conn, "1", "INBOX", "No date header", "a@b.com", "c@d.com",
            "", [], "s", "b", False, "<m1@t.com>",
            internal_date=datetime(2026, 5, 14, 10, 25, 2, tzinfo=timezone.utc),
        )
        rows = list_messages_with_threading(conn, "INBOX")
        assert len(rows) == 2
        assert rows[0][1] == "No date header"
        assert rows[1][1] == "Old date header"
