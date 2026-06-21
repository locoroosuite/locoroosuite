import json
from unittest.mock import patch, MagicMock

from tests.mail.test_message import MOCK_MSG


def _msg_with_flags(flags):
    msg = dict(MOCK_MSG)
    msg["flags"] = json.dumps(flags)
    return msg


def _protect_starred_settings(protect_starred=True):
    s = MagicMock()
    s.protect_starred = protect_starred
    s.protected_folders = None
    s.locked_keyword_prefs = None
    return s


class TestBulkDeleteProtection:
    """The /mail/bulk route previously bypassed message_is_protected (HLD U5.15d).
    These tests lock down the fix: protected messages are skipped on delete and
    on move-to-Trash, while unprotected messages and non-Trash moves proceed."""

    def test_bulk_delete_skips_starred_message(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap = MagicMock()
        protected_msg = _msg_with_flags(["\\Flagged"])
        with patch("app.modules.mail.controllers.bulk.open_cache", return_value=MagicMock()), \
             patch("app.modules.mail.controllers.bulk.get_message", return_value=protected_msg), \
             patch("app.modules.mail.controllers.bulk._get_or_create_settings", return_value=_protect_starred_settings()), \
             patch("app.modules.mail.controllers.bulk._imap_for_account", return_value=(mock_imap, MagicMock())), \
             patch("app.modules.mail.controllers.bulk.select_folder"), \
             patch("app.modules.mail.controllers.bulk.move_message") as mock_move:
            resp = client.post(
                "/app/mail/bulk",
                data={"action": "delete", "account_id": str(account_id), "message_ids": ["1"]},
            )
        assert resp.status_code == 302
        mock_move.assert_not_called()

    def test_bulk_delete_skips_locked_message(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap = MagicMock()
        with patch("app.modules.mail.controllers.bulk.open_cache", return_value=MagicMock()), \
             patch("app.modules.mail.controllers.bulk.get_message", return_value=_msg_with_flags(["$Locked"])), \
             patch("app.modules.mail.controllers.bulk._get_or_create_settings", return_value=_protect_starred_settings()), \
             patch("app.modules.mail.controllers.bulk._imap_for_account", return_value=(mock_imap, MagicMock())), \
             patch("app.modules.mail.controllers.bulk.select_folder"), \
             patch("app.modules.mail.controllers.bulk.move_message") as mock_move:
            resp = client.post(
                "/app/mail/bulk",
                data={"action": "delete", "account_id": str(account_id), "message_ids": ["1"]},
            )
        assert resp.status_code == 302
        mock_move.assert_not_called()

    def test_bulk_delete_unprotected_message_proceeds(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap = MagicMock()
        with patch("app.modules.mail.controllers.bulk.open_cache", return_value=MagicMock()), \
             patch("app.modules.mail.controllers.bulk.get_message", return_value=_msg_with_flags(["\\Seen"])), \
             patch("app.modules.mail.controllers.bulk._get_or_create_settings", return_value=_protect_starred_settings()), \
             patch("app.modules.mail.controllers.bulk._imap_for_account", return_value=(mock_imap, MagicMock())), \
             patch("app.modules.mail.controllers.bulk.select_folder"), \
             patch("app.modules.mail.controllers.bulk.move_message") as mock_move:
            resp = client.post(
                "/app/mail/bulk",
                data={"action": "delete", "account_id": str(account_id), "message_ids": ["1"]},
            )
        assert resp.status_code == 302
        mock_move.assert_called_once_with(mock_imap, "100", "Trash")

    def test_bulk_move_to_trash_skips_protected(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap = MagicMock()
        with patch("app.modules.mail.controllers.bulk.open_cache", return_value=MagicMock()), \
             patch("app.modules.mail.controllers.bulk.get_message", return_value=_msg_with_flags(["\\Flagged"])), \
             patch("app.modules.mail.controllers.bulk._get_or_create_settings", return_value=_protect_starred_settings()), \
             patch("app.modules.mail.controllers.bulk._imap_for_account", return_value=(mock_imap, MagicMock())), \
             patch("app.modules.mail.controllers.bulk.select_folder"), \
             patch("app.modules.mail.controllers.bulk.move_message") as mock_move:
            resp = client.post(
                "/app/mail/bulk",
                data={"action": "move", "destination": "Trash", "account_id": str(account_id), "message_ids": ["1"]},
            )
        assert resp.status_code == 302
        mock_move.assert_not_called()

    def test_bulk_move_to_real_folder_allowed_when_protected(self, app, authed_client):
        # U5.15d: reorganizing (move to a non-Trash folder) stays allowed even when protected.
        client, user_id, account_id = authed_client
        mock_imap = MagicMock()
        with patch("app.modules.mail.controllers.bulk.open_cache", return_value=MagicMock()), \
             patch("app.modules.mail.controllers.bulk.get_message", return_value=_msg_with_flags(["\\Flagged"])), \
             patch("app.modules.mail.controllers.bulk._get_or_create_settings", return_value=_protect_starred_settings()), \
             patch("app.modules.mail.controllers.bulk._imap_for_account", return_value=(mock_imap, MagicMock())), \
             patch("app.modules.mail.controllers.bulk.select_folder"), \
             patch("app.modules.mail.controllers.bulk.move_message") as mock_move:
            resp = client.post(
                "/app/mail/bulk",
                data={"action": "move", "destination": "Archive", "account_id": str(account_id), "message_ids": ["1"]},
            )
        assert resp.status_code == 302
        mock_move.assert_called_once_with(mock_imap, "100", "Archive")
