from unittest.mock import patch, MagicMock
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email import encoders
import json

import pytest


MOVE_URL = "/app/mail/message/{account_id}/{message_id}/move"

MOCK_MSG = (
    1, "100", "INBOX", "Test Subject", "sender@test.com", "recip@test.com",
    "date", '["\\\\Seen"]', "body text", "", "<p>html body</p>", 0,
    "<msg-id@test.com>", "thread-123", "",
)


def _make_message_with_attachment(filename="report.docx", content=b"fake-docx-data", content_type="application/octet-stream"):
    msg = MIMEMultipart()
    msg["Subject"] = "Test"
    msg["From"] = "sender@test.com"
    msg["To"] = "recip@test.com"
    text_part = MIMEText("Hello world", "plain")
    msg.attach(text_part)
    att_part = MIMEBase("application", "octet-stream")
    att_part.set_payload(content)
    encoders.encode_base64(att_part)
    att_part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(att_part)
    return msg


class TestMoveMessageValidation:
    def test_move_requires_auth(self, client):
        resp = client.post(
            MOVE_URL.format(account_id=1, message_id=1),
            data={"destination": "Archive"},
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_move_missing_destination_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = client.post(
            MOVE_URL.format(account_id=account_id, message_id=999),
            data={},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "destination" in data["error"].lower()

    def test_move_missing_destination_non_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = client.post(
            MOVE_URL.format(account_id=account_id, message_id=999),
            data={},
        )
        assert resp.status_code == 302

    def test_move_nonexistent_message_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = None
            mock_cache.return_value = mock_conn
            resp = client.post(
                MOVE_URL.format(account_id=account_id, message_id=999),
                data={"destination": "Archive"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert "not found" in data["error"].lower()

    def test_move_to_same_folder_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = (
                1, 100, "INBOX", "subject", "sender", "recip", "date", "[]", "body", "", None, 0, "msgid", None, ""
            )
            mock_cache.return_value = mock_conn
            resp = client.post(
                MOVE_URL.format(account_id=account_id, message_id=1),
                data={"destination": "INBOX"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "already" in data["error"].lower()

    def test_move_nonexistent_account(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = client.post(
            MOVE_URL.format(account_id=99999, message_id=1),
            data={"destination": "Archive"},
        )
        assert resp.status_code == 404

    def test_move_success_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = (
                1, 100, "INBOX", "subject", "sender", "recip", "date", "[]", "body", "", None, 0, "msgid", None, ""
            )
            mock_cache.return_value = mock_conn
            mock_client = MagicMock()
            mock_client.select.return_value = ("OK", [b"1"])
            mock_client._quote = lambda x: x
            mock_imap.return_value = (mock_client, MagicMock())
            resp = client.post(
                MOVE_URL.format(account_id=account_id, message_id=1),
                data={"destination": "Archive"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert data["destination"] == "Archive"

    def test_move_success_non_xhr_redirects(self, app, authed_client):
        client, user_id, account_id = authed_client
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = (
                1, 100, "INBOX", "subject", "sender", "recip", "date", "[]", "body", "", None, 0, "msgid", None, ""
            )
            mock_cache.return_value = mock_conn
            mock_client = MagicMock()
            mock_client.select.return_value = ("OK", [b"1"])
            mock_client._quote = lambda x: x
            mock_imap.return_value = (mock_client, MagicMock())
            resp = client.post(
                MOVE_URL.format(account_id=account_id, message_id=1),
                data={"destination": "Archive"},
            )
        assert resp.status_code == 302
        assert "Archive" in resp.headers.get("Location", "")

    def test_move_imap_error_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = (
                1, 100, "INBOX", "subject", "sender", "recip", "date", "[]", "body", "", None, 0, "msgid", None, ""
            )
            mock_cache.return_value = mock_conn
            mock_imap.side_effect = Exception("IMAP connection failed")
            resp = client.post(
                MOVE_URL.format(account_id=account_id, message_id=1),
                data={"destination": "Archive"},
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            )
        assert resp.status_code == 500


class TestMessageView:
    def test_message_view(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (MOCK_MSG, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = [{
                "id": 1, "uid": "100", "folder": "INBOX",
                "subject": "Test Subject", "sender": "sender@test.com",
                "sender_display": "sender", "sender_tooltip": "sender@test.com",
                "recipients": "recip@test.com", "recipients_display": "recip",
                "date": "date", "date_display": "Jan 1", "date_ts": 0,
                "flags": ["\\Seen"], "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_current": True, "snippet": "body",
                "body_html": "<html></html>", "has_attachments": False,
                "cc": "",
            }]
            resp = client.get(url)
        assert resp.status_code == 200

    def test_message_preview(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/preview"
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._snippet_debug_enabled") as mock_snippet:
            mock_load.return_value = (MOCK_MSG, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_snippet.return_value = False
            resp = client.get(url)
        assert resp.status_code == 200

    def test_mark_message_read_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/mark"
        mock_client = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = MOCK_MSG
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message._parse_flags") as mock_parse, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.set_flag"), \
             patch("app.modules.mail.controllers.message.update_flags"):
            mock_cache.return_value = mock_conn
            mock_get.return_value = MOCK_MSG
            mock_parse.return_value = []
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            resp = client.post(
                url,
                data={"action": "read"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"

    def test_flag_message_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/flag"
        mock_client = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = MOCK_MSG
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message._parse_flags") as mock_parse, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.set_flag"), \
             patch("app.modules.mail.controllers.message.update_flags"):
            mock_cache.return_value = mock_conn
            mock_get.return_value = MOCK_MSG
            mock_parse.return_value = []
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            resp = client.post(
                url,
                data={"action": "add"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"

    def test_delete_message_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/delete"
        mock_client = MagicMock()
        mock_conn = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message._parse_flags") as mock_parse, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.move_message"), \
             patch("app.modules.mail.controllers.message._set_undo_action") as mock_undo, \
             patch("app.modules.mail.controllers.message._current_undo_action") as mock_current:
            mock_cache.return_value = mock_conn
            mock_get.return_value = MOCK_MSG
            mock_parse.return_value = []
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            mock_undo.return_value = "token"
            mock_current.return_value = None
            resp = client.post(url, headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        mock_undo.assert_called_once()
        undo_args = mock_undo.call_args
        assert undo_args[0][3] == "<msg-id@test.com>"

    def test_archive_message_xhr(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/archive"
        mock_client = MagicMock()
        mock_conn = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message._parse_flags") as mock_parse, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.move_message"), \
             patch("app.modules.mail.controllers.message.create_folder"), \
             patch("app.modules.mail.controllers.message._set_undo_action") as mock_undo, \
             patch("app.modules.mail.controllers.message._current_undo_action") as mock_current:
            mock_cache.return_value = mock_conn
            mock_get.return_value = MOCK_MSG
            mock_parse.return_value = []
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            mock_undo.return_value = "token"
            mock_current.return_value = None
            resp = client.post(url, headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        mock_undo.assert_called_once()
        undo_args = mock_undo.call_args
        assert undo_args[0][3] == "<msg-id@test.com>"

    def test_download_message(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/download"
        mock_client = MagicMock()
        mock_conn = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.fetch_raw_message") as mock_fetch:
            mock_cache.return_value = mock_conn
            mock_get.return_value = MOCK_MSG
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            mock_fetch.return_value = b"raw data"
            resp = client.get(url)
        assert resp.status_code == 200
        assert "message/rfc822" in resp.content_type

    def test_print_message(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/print"
        mock_conn = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get:
            mock_cache.return_value = mock_conn
            mock_get.return_value = MOCK_MSG
            resp = client.get(url)
        assert resp.status_code == 200

    def test_print_message_shows_cc(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/print"
        msg_with_cc = (
            1, "100", "INBOX", "Test Subject", "sender@test.com", "recip@test.com",
            "date", '["\\\\Seen"]', "body text", "", None, 0,
            "<msg-id@test.com>", "thread-123", "cc-person@test.com",
        )
        mock_conn = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get:
            mock_cache.return_value = mock_conn
            mock_get.return_value = msg_with_cc
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "cc-person@test.com" in html

    def test_print_message_no_cc(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/print"
        mock_conn = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get:
            mock_cache.return_value = mock_conn
            mock_get.return_value = MOCK_MSG
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Cc" not in html


class TestMessageViewDraft:
    MOCK_DRAFT_MSG = (
        1, "100", "Drafts", "Test Subject", "sender@test.com", "recip@test.com",
        "date", '["\\\\Draft"]', "body text", "", None, 0,
        "<msg-id@test.com>", "thread-123", "",
    )

    def test_draft_message_shows_draft_banner(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (self.MOCK_DRAFT_MSG, "<p>body</p>", [], ["\\Draft"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = [{
                "id": 1, "uid": "100", "folder": "Drafts",
                "subject": "Test Subject", "sender": "sender@test.com",
                "sender_display": "sender", "sender_tooltip": "sender@test.com",
                "recipients": "recip@test.com", "recipients_display": "recip",
                "date": "date", "date_display": "Jan 1", "date_ts": 0,
                "flags": ["\\Draft"], "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_draft": True, "is_current": True,
                "snippet": "body", "body_html": "<html></html>",
                "has_attachments": False, "cc": "",
            }]
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Draft" in html
        assert "Edit draft" in html
        assert "Discard" in html

    def test_non_draft_message_no_draft_banner(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (MOCK_MSG, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = [{
                "id": 1, "uid": "100", "folder": "INBOX",
                "subject": "Test Subject", "sender": "sender@test.com",
                "sender_display": "sender", "sender_tooltip": "sender@test.com",
                "recipients": "recip@test.com", "recipients_display": "recip",
                "date": "date", "date_display": "Jan 1", "date_ts": 0,
                "flags": ["\\Seen"], "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_draft": False, "is_current": True,
                "snippet": "body", "body_html": "<html></html>",
                "has_attachments": False, "cc": "",
            }]
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "This message has not been sent yet" not in html

    def test_thread_draft_shows_edit_discard_actions(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (MOCK_MSG, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = [
                {
                    "id": 1, "uid": "100", "folder": "INBOX",
                    "subject": "Test Subject", "sender": "sender@test.com",
                    "sender_display": "sender", "sender_tooltip": "sender@test.com",
                    "recipients": "recip@test.com", "recipients_display": "recip",
                    "date": "date", "date_display": "Jan 1", "date_ts": 0,
                    "flags": ["\\Seen"], "is_unread": False, "is_flagged": False,
                    "is_sent": False, "is_draft": False, "is_current": True,
                    "snippet": "body", "body_html": "<html></html>",
                    "has_attachments": False, "cc": "",
                },
                {
                    "id": 2, "uid": "50", "folder": "Drafts",
                    "subject": "Re: Test Subject", "sender": "me@test.com",
                    "sender_display": "me", "sender_tooltip": "me@test.com",
                    "recipients": "sender@test.com", "recipients_display": "sender",
                    "date": "date2", "date_display": "Jan 2", "date_ts": 0,
                    "flags": ["\\Draft"], "is_unread": False, "is_flagged": False,
                    "is_sent": False, "is_draft": True, "is_current": False,
                    "snippet": "draft body", "body_html": "<html></html>",
                    "has_attachments": False, "cc": "",
                },
            ]
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f"draft/{account_id}/50/discard" in html
        assert "draft_uid=50" in html


class TestLoadMessageDetailReturnShape:
    def test_not_found_returns_seven_values(self, app, authed_client):
        client, user_id, account_id = authed_client
        with app.test_request_context():
            with patch("app.modules.mail.controllers.helpers.open_cache") as mock_cache, \
                 patch("app.modules.mail.controllers.helpers.get_user_key"):
                mock_cache.return_value.execute.return_value.fetchone.return_value = None
                account = type("FakeAccount", (), {
                    "cache_db_path": "/tmp/test.db",
                    "encrypted_secret": None,
                    "id": 99999,
                })()
                from app.modules.mail.controllers.helpers import _load_message_detail
                result = _load_message_detail(account, 999999)
        assert len(result) == 7
        assert result[0] is None
        assert result[6] == ""

    def test_not_found_returns_seven_values_via_controller(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/999999"
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load:
            mock_load.return_value = (None, None, None, None, None, None, "")
            resp = client.get(url)
        assert resp.status_code == 302


class TestAttachmentDownload:
    def test_download_attachment_success(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0"
        raw_msg = _make_message_with_attachment("report.docx", b"docx-content")
        mock_client = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.fetch_message") as mock_fetch:
            mock_cache.return_value = MagicMock()
            mock_get.return_value = MOCK_MSG
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            mock_fetch.return_value = raw_msg
            resp = client.get(url)
        assert resp.status_code == 200
        assert resp.data == b"docx-content"

    def test_download_attachment_not_found_message(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/999/attachment/0"
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get:
            mock_cache.return_value = MagicMock()
            mock_get.return_value = None
            resp = client.get(url)
        assert resp.status_code == 302

    def test_download_attachment_index_out_of_range(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/99"
        raw_msg = _make_message_with_attachment("report.docx", b"docx-content")
        mock_client = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.fetch_message") as mock_fetch:
            mock_cache.return_value = MagicMock()
            mock_get.return_value = MOCK_MSG
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            mock_fetch.return_value = raw_msg
            resp = client.get(url)
        assert resp.status_code == 302

    def test_download_attachment_filename_with_newline(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0"
        raw_msg = _make_message_with_attachment("report\n.docx", b"docx-content")
        mock_client = MagicMock()
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.get_message") as mock_get, \
             patch("app.modules.mail.controllers.message.decrypt_with_key") as mock_decrypt, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.select_folder"), \
             patch("app.modules.mail.controllers.message.fetch_message") as mock_fetch:
            mock_cache.return_value = MagicMock()
            mock_get.return_value = MOCK_MSG
            mock_decrypt.return_value = "secret"
            mock_imap.return_value = (mock_client, MagicMock())
            mock_fetch.return_value = raw_msg
            resp = client.get(url)
        assert resp.status_code == 200
        assert resp.data == b"docx-content"
        assert "report .docx" in resp.headers.get("Content-Disposition", "")
    def test_view_attachment_docx_success(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch, \
             patch("app.shared.pandoc_formats.convert_to_html") as mock_convert:
            mock_fetch.return_value = ("report.docx", b"docx-content", "application/octet-stream")
            mock_convert.return_value = "<html><body>Report content</body></html>"
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "report.docx" in html
        assert "Report content" in html

    def test_view_attachment_txt_success(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch, \
             patch("app.shared.pandoc_formats.convert_to_html") as mock_convert:
            mock_fetch.return_value = ("notes.txt", b"some text", "text/plain")
            mock_convert.return_value = "<html><body>some text</body></html>"
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "notes.txt" in html

    def test_view_attachment_non_viewable_redirects_to_download(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch:
            mock_fetch.return_value = ("archive.zip", b"zip-data", "application/zip")
            resp = client.get(url)
        assert resp.status_code == 302
        assert "/attachment/0" in resp.headers["Location"]
        assert "/view" not in resp.headers["Location"]

    def test_view_attachment_pandoc_failure_redirects_to_download(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch, \
             patch("app.shared.pandoc_formats.convert_to_html") as mock_convert:
            mock_fetch.return_value = ("report.docx", b"docx-content", "application/octet-stream")
            mock_convert.return_value = None
            resp = client.get(url)
        assert resp.status_code == 302

    def test_view_attachment_message_not_found(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch:
            mock_fetch.return_value = (None, None, None)
            resp = client.get(url)
        assert resp.status_code == 302

    def test_view_attachment_requires_auth(self, client):
        resp = client.get("/app/mail/message/1/1/attachment/0/view")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_view_attachment_html_has_open_in_docs_button(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch, \
             patch("app.shared.pandoc_formats.convert_to_html") as mock_convert:
            mock_fetch.return_value = ("report.docx", b"docx-content", "application/octet-stream")
            mock_convert.return_value = "<html><body>Report</body></html>"
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "open-in-docs-btn" in html
        assert "Open in Docs" in html

    def test_view_attachment_pdf_served_inline(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch:
            mock_fetch.return_value = ("document.pdf", b"%PDF-1.4 fake", "application/pdf")
            resp = client.get(url)
        assert resp.status_code == 200
        assert resp.content_type == "application/pdf"
        assert "inline" in resp.headers.get("Content-Disposition", "")

    def test_view_attachment_image_served_inline(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0/view"
        with patch("app.modules.mail.controllers.message._fetch_attachment_bytes") as mock_fetch:
            mock_fetch.return_value = ("photo.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")
            resp = client.get(url)
        assert resp.status_code == 200
        assert "image/jpeg" in resp.content_type
        assert "inline" in resp.headers.get("Content-Disposition", "")


class TestDownloadAttachmentInline:
    def test_download_uses_attachment_disposition_by_default(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0"
        msg = _make_message_with_attachment("photo.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.fetch_message", return_value=msg):
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = (
                1, 100, "INBOX", "subject", "sender", "recip", "date", "[]", "body", "", None, 0, "msgid", None, ""
            )
            mock_cache.return_value = mock_conn
            mock_client = MagicMock()
            mock_client.select.return_value = ("OK", [b"1"])
            mock_client._quote = lambda x: x
            mock_imap.return_value = (mock_client, MagicMock())
            resp = client.get(url)
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_download_uses_inline_disposition_with_query_param(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1/attachment/0?inline=1"
        msg = _make_message_with_attachment("photo.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")
        with patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.message.fetch_message", return_value=msg):
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = (
                1, 100, "INBOX", "subject", "sender", "recip", "date", "[]", "body", "", None, 0, "msgid", None, ""
            )
            mock_cache.return_value = mock_conn
            mock_client = MagicMock()
            mock_client.select.return_value = ("OK", [b"1"])
            mock_client._quote = lambda x: x
            mock_imap.return_value = (mock_client, MagicMock())
            resp = client.get(url)
        assert resp.status_code == 200
        assert "inline" in resp.headers.get("Content-Disposition", "")
