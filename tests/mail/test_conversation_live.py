"""U4.7e/U4.7f: conversation visibility and live updates.

Covers the "conversations don't feel grouped" change set:

- Sent copies append with ``\\Seen`` (the web send worker), are recorded in
  the local cache immediately, and push a ``ui_change`` SSE event (B/C).
- The API send path pushes the same ``ui_change`` event (parity).
- ``record_sent_copy`` parses a raw sent message into a read, threaded
  cache row (B).
- Folder-list visual grouping: the left accent is reserved for
  multi-message threads and the head row shows a participant summary (D).
- The thread-fragment route powering the conversation-view live update (E):
  auth, 404s, only-new filtering, response shape.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def seeded_cache(app, authed_client, tmp_path):
    """Real SQLCipher cache with one 3-message conversation (2 INBOX + 1
    Sent) and one standalone INBOX message. Returns (cache_path, ids)."""
    import sqlcipher3

    from app.modules.mail.services.cache_db import init_cache_schema, upsert_message
    from app.shared.db import db as _db
    from app.shared.models.core import CustomerAccount

    _client, _user_id, account_id = authed_client
    cache_path = str(tmp_path / "conversation.db")
    conn = sqlcipher3.connect(cache_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
    init_cache_schema(conn)

    ids = {
        "alice": upsert_message(
            conn,
            "1",
            "INBOX",
            "Re: Project",
            "Alice Smith <alice@example.com>",
            "me@example.com",
            "Mon, 1 Jan 2024 10:00:00 +0000",
            [],
            "alice snippet",
            "alice body",
            False,
            "<m1@example.com>",
            thread_id="thread-1",
        ),
        "bob": upsert_message(
            conn,
            "2",
            "INBOX",
            "Re: Project",
            "Bob Jones <bob@example.com>",
            "me@example.com",
            "Tue, 2 Jan 2024 10:00:00 +0000",
            [],
            "bob snippet",
            "bob body",
            False,
            "<m2@example.com>",
            thread_id="thread-1",
        ),
        "sent": upsert_message(
            conn,
            "3",
            "Sent",
            "Re: Project",
            "Me <me@example.com>",
            "alice@example.com",
            "Wed, 3 Jan 2024 10:00:00 +0000",
            ["\\Seen"],
            "sent snippet",
            "sent body",
            False,
            "<m3@example.com>",
            thread_id="thread-1",
        ),
        "solo": upsert_message(
            conn,
            "4",
            "INBOX",
            "Standalone",
            "Carol White <carol@example.com>",
            "me@example.com",
            "Thu, 4 Jan 2024 10:00:00 +0000",
            [],
            "solo snippet",
            "solo body",
            False,
            "<m4@example.com>",
            thread_id="thread-2",
        ),
    }
    conn.commit()
    conn.close()

    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        assert account is not None
        account.cache_db_path = cache_path
        _db.session.commit()
    return cache_path, ids


class TestSendWorkerSentCopy:
    """B + C: the web send worker stores the Sent copy read, records it in
    the cache, and pushes a ui_change event (U4.7e/U4.7f)."""

    @staticmethod
    def _payload(user_id, account_id, domain_id):
        return {
            "user_id": user_id,
            "account_id": account_id,
            "domain_id": domain_id,
            "auth_type": "password",
            "secret": "pw",
            "from_addr": "test@example.com",
            "to_addrs": "dest@example.com",
            "cc_addrs": "",
            "bcc_addrs": "",
            "subject": "Hello",
            "body_html": "",
            "request_receipt": False,
            "attachments_count": 0,
            "msg": b"raw wire message",
            "sent_msg": b"raw sent copy",
            "draft_uid": None,
            "status": "countdown",
            "send_after": 0,
            "created_at": 0,
            "updated_at": 0,
            "error": None,
            "warning": None,
        }

    def test_sent_copy_appended_seen_cached_and_pushed(self, app, authed_client):
        from app.modules.mail.controllers import helpers as mail_helpers
        from app.shared.db import db as _db
        from app.shared.models.core import Domain

        _client, user_id, account_id = authed_client
        with app.app_context():
            domain = _db.session.query(Domain).first()
            assert domain is not None
            payload = self._payload(user_id, account_id, domain.id)
        token = "tok-seen"
        try:
            with mail_helpers._pending_sends_lock:
                mail_helpers._pending_sends[token] = payload
            with (
                patch.object(mail_helpers, "smtp_connect"),
                patch.object(mail_helpers, "smtp_login"),
                patch.object(mail_helpers, "smtp_send"),
                patch.object(mail_helpers, "_imap_for_account", return_value=(MagicMock(), None)),
                patch.object(mail_helpers, "safe_logout"),
                patch.object(
                    mail_helpers,
                    "ensure_folder_and_append",
                    return_value=("OK", [b"[APPENDUID 1 42]"]),
                ) as mock_append,
                patch.object(mail_helpers, "parse_append_uid", return_value="42"),
                patch.object(mail_helpers, "open_cache", return_value=MagicMock()),
                patch.object(mail_helpers, "record_sent_copy") as mock_record,
                patch.object(mail_helpers, "push_ui_event") as mock_push,
            ):
                mail_helpers._send_worker(app, token)

            assert mail_helpers._pending_sends[token]["status"] == "sent"
            # U4.7e: the sender's own copy is stored read.
            _args, kwargs = mock_append.call_args
            assert kwargs.get("flags") == ["\\Seen"]
            # U4.7e/U4.7f: cache record + SSE push right after the append.
            record_args = mock_record.call_args[0]
            assert record_args[1] == "Sent"
            assert record_args[2] == "42"
            assert record_args[3] == b"raw sent copy"
            mock_push.assert_called_once_with(
                user_id, "mail", "send_complete", {"account_id": account_id}
            )
        finally:
            with mail_helpers._pending_sends_lock:
                mail_helpers._pending_sends.pop(token, None)

    def test_append_failure_skips_cache_record_and_push(self, app, authed_client):
        from app.modules.mail.controllers import helpers as mail_helpers
        from app.shared.db import db as _db
        from app.shared.models.core import Domain

        _client, user_id, account_id = authed_client
        with app.app_context():
            domain = _db.session.query(Domain).first()
            assert domain is not None
            payload = self._payload(user_id, account_id, domain.id)
        token = "tok-append-fail"
        try:
            with mail_helpers._pending_sends_lock:
                mail_helpers._pending_sends[token] = payload
            with (
                patch.object(mail_helpers, "smtp_connect"),
                patch.object(mail_helpers, "smtp_login"),
                patch.object(mail_helpers, "smtp_send"),
                patch.object(mail_helpers, "_imap_for_account", return_value=(MagicMock(), None)),
                patch.object(mail_helpers, "safe_logout"),
                patch.object(mail_helpers, "ensure_folder_and_append", return_value=("NO", None)),
                patch.object(mail_helpers, "record_sent_copy") as mock_record,
                patch.object(mail_helpers, "push_ui_event") as mock_push,
            ):
                mail_helpers._send_worker(app, token)

            # Message still counts as sent, but nothing new landed in the
            # cache, so no record/push happens. The later Sent sync's
            # sync_status event refreshes open views instead.
            assert mail_helpers._pending_sends[token]["status"] == "sent"
            mock_record.assert_not_called()
            mock_push.assert_not_called()
        finally:
            with mail_helpers._pending_sends_lock:
                mail_helpers._pending_sends.pop(token, None)


class TestRecordSentCopy:
    def test_upserts_read_threaded_row(self, tmp_path):
        import sqlcipher3

        from app.modules.mail.services.cache_db import (
            get_message_by_uid_and_folder,
            init_cache_schema,
        )
        from app.modules.mail.services.imap_sync import record_sent_copy

        conn = sqlcipher3.connect(str(tmp_path / "sent.db"))
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        raw = (
            b"From: Me <me@example.com>\r\n"
            b"To: Alice <alice@example.com>\r\n"
            b"Subject: Re: Project\r\n"
            b"Message-ID: <sent-1@example.com>\r\n"
            b"In-Reply-To: <m1@example.com>\r\n"
            b"References: <m1@example.com>\r\n"
            b"Date: Wed, 3 Jan 2024 10:00:00 +0000\r\n"
            b'Content-Type: multipart/alternative; boundary="BOUND"\r\n'
            b"\r\n"
            b"--BOUND\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"sent body\r\n"
            b"--BOUND\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"\r\n"
            b"<p>sent body</p>\r\n"
            b"--BOUND--\r\n"
        )
        row_id = record_sent_copy(conn, "Sent", "7", raw)
        assert row_id

        row = get_message_by_uid_and_folder(conn, "7", "Sent")
        assert row is not None
        assert "Re: Project" in row["subject"]
        flags = json.loads(row["flags"])
        # U4.7e: never stored unread.
        assert "\\Seen" in flags
        # Threading headers survive so the copy joins the conversation.
        assert row["thread_id"]
        assert row["body_html"]
        conn.close()


class TestApiSendUiEvent:
    def test_send_message_pushes_ui_change(self):
        from app.modules.mail.services import send as send_service

        account = MagicMock()
        account.customer_id = 5
        account.id = 7
        account.email_address = "me@example.com"
        domain = MagicMock()

        with (
            patch.object(send_service, "_build_and_send_smtp"),
            patch.object(send_service, "_append_to_sent", return_value=(MagicMock(), "9", "<m@x>")),
            patch("app.shared.ui_events.push_event") as mock_push,
        ):
            result = send_service.send_message(
                account, domain, "secret", to=["dest@example.com"], subject="S", body_plain="hi"
            )
        assert result["status"] == "sent"
        mock_push.assert_called_once()
        customer_id, event_type, data = mock_push.call_args[0]
        assert customer_id == 5
        assert event_type == "ui_change"
        assert data["module"] == "mail"
        assert data["action"] == "send_complete"
        assert data["account_id"] == 7


class TestFolderListVisualGrouping:
    """D: accent reserved for conversations + participant summary (U4.7e)."""

    def test_conversation_accent_and_participants(self, authed_client, seeded_cache):
        client, _user_id, account_id = authed_client
        _cache_path, _ids = seeded_cache
        resp = client.get(f"/app/mail/folder/{account_id}/INBOX/messages")
        assert resp.status_code == 200
        data = resp.get_json()
        html = data["html"]

        # Multi-message thread carries the accent + participant summary.
        assert "border-l-2 border-l-indigo-400" in html
        assert "Alice Smith, Bob Jones, You" in html
        # Single-message threads render bare: the accent is a grouping
        # signal, not uniform chrome.
        assert 'class="thread-card" data-thread-card' in html
        assert data["total_threads"] == 2


class TestThreadFragment:
    """E: the conversation-view live-update fragment route (U4.7f)."""

    def test_returns_only_new_messages(self, authed_client, seeded_cache):
        client, _user_id, account_id = authed_client
        _cache_path, ids = seeded_cache
        url = (
            f"/app/mail/message/{account_id}/{ids['alice']}/thread-fragment"
            f"?have={ids['bob']},{ids['sent']}"
        )
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["new_ids"] == [ids["alice"]]
        assert set(data["ids"]) == {ids["alice"], ids["bob"], ids["sent"]}
        assert data["count"] == 3
        assert str(ids["alice"]) in data["bodies"]
        assert f'data-thread-msg-id="{ids["alice"]}"' in data["html"]
        assert f'data-thread-msg-id="{ids["bob"]}"' not in data["html"]
        # Insertion ordering key for the client-side live update.
        assert "data-thread-sort-ts" in data["html"]

    def test_all_messages_when_have_empty(self, authed_client, seeded_cache):
        client, _user_id, account_id = authed_client
        _cache_path, ids = seeded_cache
        resp = client.get(f"/app/mail/message/{account_id}/{ids['alice']}/thread-fragment")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data["new_ids"]) == {ids["alice"], ids["bob"], ids["sent"]}

    def test_unknown_message_404(self, authed_client, seeded_cache):
        client, _user_id, account_id = authed_client
        resp = client.get(f"/app/mail/message/{account_id}/999999/thread-fragment")
        assert resp.status_code == 404
        assert resp.get_json()["error"]["code"] == "NOT_FOUND"

    def test_other_account_404(self, authed_client, seeded_cache):
        client, _user_id, account_id = authed_client
        resp = client.get(f"/app/mail/message/{account_id + 999}/1/thread-fragment")
        assert resp.status_code == 404

    def test_unauthenticated_redirects(self, app, authed_client, seeded_cache):
        # require_customer redirects anonymous sessions to login.
        with app.test_client() as anon:
            resp = anon.get(f"/app/mail/message/{authed_client[2]}/1/thread-fragment")
            assert resp.status_code == 302
