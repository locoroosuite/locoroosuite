import imaplib
import json
from unittest.mock import MagicMock, patch

from app.modules.mail.services.spam import (
    SpamFlagUnsupportedError,
    SpamFolderMissingError,
    not_spam,
    report_spam,
    set_spam_action_enabled,
    spam_action_enabled,
    spam_destination,
)
from app.shared.models.core import CustomerSettings

JUNK_URL = "/app/mail/message/{account_id}/{message_id}/junk"
NOT_JUNK_URL = "/app/mail/message/{account_id}/{message_id}/not-junk"
UNDO_URL = "/app/mail/message/undo"

MOCK_MSG = {
    "id": 1,
    "uid": "100",
    "folder": "INBOX",
    "subject": "Test Subject",
    "sender": "sender@test.com",
    "recipients": "recip@test.com",
    "date": "date",
    "flags": '["\\\\Seen"]',
    "snippet": "body text",
    "has_attachments": 0,
    "message_id": "<msg-id@test.com>",
    "thread_id": "thread-123",
    "cc": "",
}


class TestSpamDestination:
    def test_literal_junk_preferred(self):
        client = MagicMock()
        with patch(
            "app.modules.mail.services.spam.list_folders", return_value=["INBOX", "Junk", "Spam"]
        ):
            assert spam_destination(client) == "Junk"

    def test_spam_fallback(self):
        client = MagicMock()
        with patch("app.modules.mail.services.spam.list_folders", return_value=["INBOX", "Spam"]):
            assert spam_destination(client) == "Spam"

    def test_alias_junk_email(self):
        client = MagicMock()
        with patch(
            "app.modules.mail.services.spam.list_folders", return_value=["INBOX", "Junk E-mail"]
        ):
            assert spam_destination(client) == "Junk E-mail"

    def test_alias_bulk_mail(self):
        client = MagicMock()
        with patch(
            "app.modules.mail.services.spam.list_folders", return_value=["INBOX", "Bulk Mail"]
        ):
            assert spam_destination(client) == "Bulk Mail"

    def test_no_junk_folder_returns_none(self):
        client = MagicMock()
        with patch(
            "app.modules.mail.services.spam.list_folders", return_value=["INBOX", "Archive"]
        ):
            assert spam_destination(client) is None


class TestReportSpamService:
    def _run(self):
        client = MagicMock()
        with (
            patch("app.modules.mail.services.spam.list_folders", return_value=["INBOX", "Junk"]),
            patch("app.modules.mail.services.spam.set_flag") as mock_set,
            patch("app.modules.mail.services.spam.move_message") as mock_move,
        ):
            destination = report_spam(client, "100")
        return client, mock_set, mock_move, destination

    def test_sets_flag_then_moves(self):
        client, mock_set, mock_move, destination = self._run()
        assert destination == "Junk"
        mock_set.assert_called_once_with(client, "100", "\\Junk", add=True)
        mock_move.assert_called_once_with(client, "100", "Junk")
        client.expunge.assert_called_once()

    def test_missing_folder_raises(self):
        client = MagicMock()
        with patch("app.modules.mail.services.spam.list_folders", return_value=["INBOX"]):
            try:
                report_spam(client, "100")
                raise AssertionError("expected SpamFolderMissingError")
            except SpamFolderMissingError:
                pass

    def test_flag_rejection_raises(self):
        client = MagicMock()
        with (
            patch("app.modules.mail.services.spam.list_folders", return_value=["INBOX", "Junk"]),
            patch(
                "app.modules.mail.services.spam.set_flag", side_effect=imaplib.IMAP4.error("nope")
            ),
        ):
            try:
                report_spam(client, "100")
                raise AssertionError("expected SpamFlagUnsupportedError")
            except SpamFlagUnsupportedError:
                pass


class TestNotSpamService:
    def test_from_junk_folder_moves_to_inbox(self):
        client = MagicMock()
        with (
            patch("app.modules.mail.services.spam.set_flag") as mock_set,
            patch("app.modules.mail.services.spam.move_message") as mock_move,
        ):
            destination = not_spam(client, "100", "Spam")
        assert destination == "INBOX"
        mock_set.assert_called_once_with(client, "100", "\\Junk", add=False)
        mock_move.assert_called_once_with(client, "100", "INBOX")
        client.expunge.assert_called_once()

    def test_outside_junk_folder_clears_flag_only(self):
        client = MagicMock()
        with (
            patch("app.modules.mail.services.spam.set_flag") as mock_set,
            patch("app.modules.mail.services.spam.move_message") as mock_move,
        ):
            destination = not_spam(client, "100", "INBOX")
        assert destination is None
        mock_set.assert_called_once_with(client, "100", "\\Junk", add=False)
        mock_move.assert_not_called()

    def test_flag_clear_failure_still_moves(self):
        client = MagicMock()
        with (
            patch(
                "app.modules.mail.services.spam.set_flag", side_effect=imaplib.IMAP4.error("nope")
            ),
            patch("app.modules.mail.services.spam.move_message") as mock_move,
        ):
            destination = not_spam(client, "100", "Junk")
        assert destination == "INBOX"
        mock_move.assert_called_once_with(client, "100", "INBOX")


class TestSpamPrefs:
    def test_default_enabled(self, app):
        with app.app_context():
            settings = CustomerSettings()
            settings.customer_id = 1
            assert spam_action_enabled(settings, 5) is True

    def test_set_persists_json(self, app):
        with app.app_context():
            settings = CustomerSettings()
            settings.customer_id = 1
            set_spam_action_enabled(settings, 5, False)
            assert json.loads(settings.spam_action_prefs) == {"5": False}
            assert spam_action_enabled(settings, 5) is False

    def test_invalid_json_treated_as_default(self, app):
        with app.app_context():
            settings = CustomerSettings()
            settings.customer_id = 1
            settings.spam_action_prefs = "{not json"
            assert spam_action_enabled(settings, 5) is True


class TestJunkRoute:
    def _post(self, client, account_id, xhr=True, **kwargs):
        headers = {"X-Requested-With": "XMLHttpRequest"} if xhr else {}
        return client.post(
            JUNK_URL.format(account_id=account_id, message_id=1), headers=headers, **kwargs
        )

    def test_happy_path_xhr(self, app, authed_client):
        client, _user_id, account_id = authed_client
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=dict(MOCK_MSG)),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch(
                "app.modules.mail.controllers.message.report_spam", return_value="Junk"
            ) as mock_report,
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = self._post(client, account_id)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert data["undo_action"]["label"] == "Reported as spam"
        assert data["undo_action"]["view_label"] == "View Junk"
        assert data["undo_action"]["action_type"] == "junk"
        mock_report.assert_called_once()

    def test_happy_path_form_redirects(self, app, authed_client):
        client, _user_id, account_id = authed_client
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=dict(MOCK_MSG)),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.report_spam", return_value="Junk"),
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = self._post(client, account_id, xhr=False)
        assert resp.status_code == 302

    def test_setting_disabled_returns_error(self, app, authed_client):
        client, user_id, account_id = authed_client
        with app.app_context():
            settings = CustomerSettings()
            settings.customer_id = user_id
            settings.spam_action_prefs = json.dumps({str(account_id): False})
            from app.shared.db import db

            db.session.add(settings)
            db.session.commit()
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=dict(MOCK_MSG)),
        ):
            mock_cache.return_value = MagicMock()
            resp = self._post(client, account_id)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "error"
        assert "disabled" in data["error"].lower()

    def test_missing_junk_folder_auto_disables(self, app, authed_client):
        client, user_id, account_id = authed_client
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=dict(MOCK_MSG)),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch(
                "app.modules.mail.controllers.message.report_spam",
                side_effect=SpamFolderMissingError(),
            ),
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = self._post(client, account_id)
        data = json.loads(resp.data)
        assert data["status"] == "error"
        assert "No Spam/Junk folder" in data["error"]
        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            assert settings is not None
            assert json.loads(settings.spam_action_prefs)[str(account_id)] is False

    def test_flag_rejection_auto_disables(self, app, authed_client):
        client, user_id, account_id = authed_client
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=dict(MOCK_MSG)),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch(
                "app.modules.mail.controllers.message.report_spam",
                side_effect=SpamFlagUnsupportedError(),
            ),
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = self._post(client, account_id)
        data = json.loads(resp.data)
        assert data["status"] == "error"
        assert "Server doesn't support Spam flags" in data["error"]
        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            assert settings is not None
            assert json.loads(settings.spam_action_prefs)[str(account_id)] is False

    def test_missing_message_redirects_to_mailbox(self, app, authed_client):
        client, _user_id, account_id = authed_client
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=None),
        ):
            mock_cache.return_value = MagicMock()
            resp = self._post(client, account_id)
        assert resp.status_code == 302


class TestNotJunkRoute:
    def _post(self, client, account_id, xhr=True):
        headers = {"X-Requested-With": "XMLHttpRequest"} if xhr else {}
        return client.post(
            NOT_JUNK_URL.format(account_id=account_id, message_id=1), headers=headers
        )

    def test_happy_path_from_junk_folder(self, app, authed_client):
        client, _user_id, account_id = authed_client
        msg = dict(MOCK_MSG, folder="Junk", flags='["\\\\Junk"]')
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=msg),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.not_spam", return_value="INBOX") as mock_ns,
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = self._post(client, account_id)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert data["undo_action"]["label"] == "Marked as not spam"
        assert data["undo_action"]["view_label"] == "View Inbox"
        assert data["undo_action"]["action_type"] == "not_spam"
        mock_ns.assert_called_once()

    def test_not_gated_by_spam_setting(self, app, authed_client):
        client, user_id, account_id = authed_client
        with app.app_context():
            settings = CustomerSettings()
            settings.customer_id = user_id
            settings.spam_action_prefs = json.dumps({str(account_id): False})
            from app.shared.db import db

            db.session.add(settings)
            db.session.commit()
        msg = dict(MOCK_MSG, folder="Spam")
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=msg),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.not_spam", return_value="INBOX"),
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = self._post(client, account_id)
        data = json.loads(resp.data)
        assert data["status"] == "ok"

    def test_form_post_redirects(self, app, authed_client):
        client, _user_id, account_id = authed_client
        msg = dict(MOCK_MSG, folder="Junk")
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=msg),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.not_spam", return_value="INBOX"),
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = self._post(client, account_id, xhr=False)
        assert resp.status_code == 302


class TestUndoSpamActions:
    def _junk(self, client, account_id):
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=dict(MOCK_MSG)),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.report_spam", return_value="Junk"),
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = client.post(
                JUNK_URL.format(account_id=account_id, message_id=1),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        return json.loads(resp.data)["undo_action"]["token"]

    def _not_junk(self, client, account_id):
        msg = dict(MOCK_MSG, folder="Junk")
        with (
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.get_message", return_value=msg),
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.select_folder"),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.not_spam", return_value="INBOX"),
        ):
            mock_cache.return_value = MagicMock()
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = client.post(
                NOT_JUNK_URL.format(account_id=account_id, message_id=1),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        return json.loads(resp.data)["undo_action"]["token"]

    def test_undo_junk_clears_flag_before_move(self, app, authed_client):
        client, _user_id, account_id = authed_client
        token = self._junk(client, account_id)
        manager = MagicMock()
        with (
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.search_header", return_value=[42]),
            patch("app.modules.mail.controllers.message.set_flag", manager.set_flag),
            patch("app.modules.mail.controllers.message.move_message", manager.move_message),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.select_folder"),
        ):
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = client.post(UNDO_URL, data={"token": token})
        assert resp.status_code == 302
        manager.set_flag.assert_called_once()
        assert manager.set_flag.call_args.kwargs.get("add") is False
        assert "\\Junk" in manager.set_flag.call_args.args
        manager.move_message.assert_called_once()
        # flag clear must precede the move
        order = [c[0] for c in manager.mock_calls]
        assert order.index("set_flag") < order.index("move_message")

    def test_undo_not_spam_resets_flag(self, app, authed_client):
        client, _user_id, account_id = authed_client
        token = self._not_junk(client, account_id)
        manager = MagicMock()
        with (
            patch("app.modules.mail.controllers.message._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.message.search_header", return_value=[42]),
            patch("app.modules.mail.controllers.message.set_flag", manager.set_flag),
            patch("app.modules.mail.controllers.message.move_message", manager.move_message),
            patch("app.modules.mail.controllers.message.safe_logout"),
            patch("app.modules.mail.controllers.message.select_folder"),
        ):
            mock_imap.return_value = (MagicMock(), "example.com")
            resp = client.post(UNDO_URL, data={"token": token})
        assert resp.status_code == 302
        manager.set_flag.assert_called_once()
        assert manager.set_flag.call_args.kwargs.get("add") is True
        manager.move_message.assert_called_once()

    def test_undo_without_action_redirects(self, app, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.post(UNDO_URL, data={"token": "bogus"})
        assert resp.status_code == 302
