from unittest.mock import patch, MagicMock
import json

import pytest


MOVE_URL = "/app/mail/message/{account_id}/{message_id}/move"


class TestMoveMessageValidation:
    def test_move_requires_auth(self, client):
        resp = client.post(MOVE_URL.format(account_id=1, message_id=1), data={"destination": "Archive"})
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
