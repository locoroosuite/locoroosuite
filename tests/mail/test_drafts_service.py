"""Unit tests for the shared draft service (U6.5b) and reply-context
derivation (U15.26). The HTTP/MCP surface is covered in tests/api and
tests/mcp; these tests pin the identifier-resolution contract itself.
"""

import pytest

from app.modules.mail.services.drafts import (
    DRAFT_FLAGS,
    DraftNotFoundError,
    build_draft_message,
    resolve_draft_identifier,
    resolve_reply_context,
)


@pytest.fixture()
def cache_path(tmp_path):
    import os

    from app.modules.mail.services.cache_db import open_cache

    path = str(tmp_path / "mail.cache.db")
    if os.path.exists(path):
        os.remove(path)
    # open_cache applies migrations; close immediately.
    conn = open_cache(path, "a" * 64)
    conn.close()
    return path


def _open(path):
    from app.modules.mail.services.cache_db import open_cache

    return open_cache(path, "a" * 64)


def _seed(path, uid, folder, message_id=None, in_reply_to=None, ref_list=None, subject="s"):
    from app.modules.mail.services.cache_db import upsert_message

    conn = _open(path)
    upsert_message(
        conn,
        uid=uid,
        folder=folder,
        subject=subject,
        sender="a@example.com",
        recipients="b@example.com",
        date="Tue, 20 May 2026 10:00:00 +0000",
        flags=["\\Draft", "\\Seen"],
        snippet="x",
        body="x",
        has_attachments=False,
        message_id=message_id or f"<{uid}@example.com>",
        in_reply_to=in_reply_to,
        ref_list=ref_list,
    )
    conn.close()


class TestResolveDraftIdentifier:
    def test_cache_id_preferred_for_digits(self, cache_path):
        _seed(cache_path, uid="343", folder="Drafts", message_id="<d1@example.com>")
        conn = _open(cache_path)
        try:
            row = conn.execute("SELECT id FROM messages WHERE uid = '343'").fetchone()
            assert row is not None
            resolved = resolve_draft_identifier(conn, row["id"])
            assert resolved is not None
            assert resolved["uid"] == "343"
        finally:
            conn.close()

    def test_uid_accepted_directly(self, cache_path):
        _seed(cache_path, uid="344", folder="Drafts")
        conn = _open(cache_path)
        try:
            resolved = resolve_draft_identifier(conn, "344")
            assert resolved is not None
            assert resolved["uid"] == "344"
        finally:
            conn.close()

    def test_drafts_alias_folders_resolved(self, cache_path):
        _seed(cache_path, uid="10", folder="INBOX.Drafts")
        conn = _open(cache_path)
        try:
            assert resolve_draft_identifier(conn, "10") is not None
        finally:
            conn.close()

    def test_inbox_message_id_not_resolved(self, cache_path):
        _seed(cache_path, uid="200", folder="INBOX")
        conn = _open(cache_path)
        try:
            assert resolve_draft_identifier(conn, "200") is None
        finally:
            conn.close()

    def test_unknown_identifier_returns_none(self, cache_path):
        _seed(cache_path, uid="1", folder="Drafts")
        conn = _open(cache_path)
        try:
            assert resolve_draft_identifier(conn, "9999") is None
            assert resolve_draft_identifier(conn, "") is None
            assert resolve_draft_identifier(conn, "abc") is None
        finally:
            conn.close()


class TestResolveReplyContext:
    def test_cache_id_derives_in_reply_to_and_references(self, cache_path):
        _seed(
            cache_path,
            uid="300",
            folder="INBOX",
            message_id="<parent@example.com>",
            ref_list="<root@example.com>",
        )
        conn = _open(cache_path)
        try:
            found = conn.execute("SELECT id FROM messages WHERE uid = '300'").fetchone()
            assert found is not None
            row_id = found["id"]
            in_reply_to, references = resolve_reply_context(conn, row_id)
            assert in_reply_to == "<parent@example.com>"
            assert references == "<root@example.com> <parent@example.com>"
        finally:
            conn.close()

    def test_unknown_cache_id_raises_value_error(self, cache_path):
        conn = _open(cache_path)
        try:
            with pytest.raises(ValueError):
                resolve_reply_context(conn, 99999)
        finally:
            conn.close()

    def test_raw_message_id_passthrough(self, cache_path):
        conn = _open(cache_path)
        try:
            in_reply_to, references = resolve_reply_context(
                conn, None, "179248033325.8@example.com"
            )
            assert in_reply_to == "<179248033325.8@example.com>"
            assert references == "<179248033325.8@example.com>"
        finally:
            conn.close()

    def test_neither_returns_none_pair(self, cache_path):
        conn = _open(cache_path)
        try:
            assert resolve_reply_context(conn, None, None) == (None, None)
        finally:
            conn.close()


class TestDraftMime:
    def test_draft_flags_are_draft_and_seen(self):
        assert DRAFT_FLAGS == ["\\Draft", "\\Seen"]

    def test_draft_message_carries_threading_headers(self):
        msg = build_draft_message(
            "me@example.com",
            "example.com",
            to=["them@example.com"],
            subject="Re: hello",
            body_plain="hi",
            in_reply_to="<orig@example.com>",
            references="<root@example.com> <orig@example.com>",
        )
        assert msg["In-Reply-To"] == "<orig@example.com>"
        assert msg["References"] == "<root@example.com> <orig@example.com>"
        assert msg["Message-ID"] is not None
        assert msg["Date"] is not None


class TestDraftNotFoundError:
    def test_carries_identifier(self):
        exc = DraftNotFoundError("1194")
        assert exc.identifier == "1194"
        assert "1194" in str(exc)
