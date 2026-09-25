import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.mail.services.spam import SpamFlagUnsupportedError, SpamFolderMissingError
from app.shared.models.core import CustomerSettings
from tests.api.conftest import auth_header, cleanup_cache_db, create_api_token, setup_cache_db


@pytest.fixture()
def spam_api(app, api_customer):
    client, user_id, account_id = api_customer
    with app.app_context():
        token_value, _ = create_api_token(app, user_id)
    cache_path = setup_cache_db(app, account_id)
    yield client, token_value, account_id, cache_path, user_id
    cleanup_cache_db(cache_path)


def _seed(cache_path, folder="INBOX", flags=None):
    from app.modules.mail.services.cache_db import open_cache, upsert_folder, upsert_message

    conn = open_cache(cache_path, "a" * 64)
    upsert_folder(conn, folder, unread_count=1)
    upsert_message(
        conn,
        uid="200",
        folder=folder,
        subject="Lose weight fast",
        sender="spam@example.net",
        recipients="api@example.com",
        date="Tue, 20 May 2026 11:00:00 +0000",
        flags=flags if flags is not None else ["\\Junk"],
        snippet="Buy now",
        body="Buy now",
        has_attachments=False,
        message_id="<spam-001@example.net>",
        thread_id="spam-thread-001",
    )
    conn.close()


def _set_account_secret(app, account_id, dek="a" * 64):
    from app.modules.mail.services.secrets import encrypt_with_key
    from app.shared.db import db as _db
    from app.shared.models.core import CustomerAccount

    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        assert account is not None
        account.encrypted_secret = encrypt_with_key("testpass", dek)
        _db.session.commit()


def _message_id(client, token, folder="INBOX"):
    resp = client.get(f"/api/v1/mail/folders/{folder}/messages", headers=auth_header(token))
    msgs = json.loads(resp.data)["data"]
    return msgs[0]["id"]


def _set_spam_pref(app, user_id, account_id, enabled):
    from app.shared.db import db as _db

    with app.app_context():
        settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
        if not settings:
            settings = CustomerSettings()
            settings.customer_id = user_id
            _db.session.add(settings)
        assert settings is not None
        settings.spam_action_prefs = json.dumps({str(account_id): enabled})
        _db.session.commit()


class TestReportSpam:
    def test_happy_path(self, app, spam_api):
        client, token, account_id, cache_path, _user_id = spam_api
        _seed(cache_path)
        _set_account_secret(app, account_id)
        msg_id = _message_id(client, token)
        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
            patch("app.api.controllers.mail.report_spam", return_value="Junk") as mock_report,
        ):
            resp = client.post(f"/api/v1/mail/messages/{msg_id}/spam", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["id"] == msg_id
        assert data["moved_to"] == "Junk"
        assert data["junk"] is True
        mock_report.assert_called_once_with(mock_imap, "200")

    def test_message_not_found(self, app, spam_api):
        client, token, _account_id, _cache_path, _user_id = spam_api
        resp = client.post("/api/v1/mail/messages/99999/spam", headers=auth_header(token))
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_requires_auth(self, app, spam_api):
        client, _token, _account_id, _cache_path, _user_id = spam_api
        resp = client.post("/api/v1/mail/messages/1/spam")
        assert resp.status_code == 401

    def test_requires_write_scope(self, app, api_customer):
        client, user_id, _account_id = api_customer
        with app.app_context():
            token_value, _ = create_api_token(app, user_id, scopes=["mail:read"])
        resp = client.post("/api/v1/mail/messages/1/spam", headers=auth_header(token_value))
        assert resp.status_code == 403

    def test_disabled_returns_409(self, app, spam_api):
        client, token, account_id, cache_path, user_id = spam_api
        _seed(cache_path)
        _set_spam_pref(app, user_id, account_id, False)
        msg_id = _message_id(client, token)
        with patch("app.api.controllers.mail._imap_connect") as mock_connect:
            resp = client.post(f"/api/v1/mail/messages/{msg_id}/spam", headers=auth_header(token))
        assert resp.status_code == 409
        data = json.loads(resp.data)
        assert data["error"]["code"] == "SPAM_ACTION_DISABLED"
        mock_connect.assert_not_called()

    def test_missing_folder_409_and_auto_disables(self, app, spam_api):
        client, token, account_id, cache_path, user_id = spam_api
        _seed(cache_path)
        _set_account_secret(app, account_id)
        msg_id = _message_id(client, token)
        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
            patch(
                "app.api.controllers.mail.report_spam",
                side_effect=SpamFolderMissingError(),
            ),
        ):
            resp = client.post(f"/api/v1/mail/messages/{msg_id}/spam", headers=auth_header(token))
        assert resp.status_code == 409
        data = json.loads(resp.data)
        assert data["error"]["code"] == "SPAM_FOLDER_MISSING"
        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            assert settings is not None
            assert json.loads(settings.spam_action_prefs)[str(account_id)] is False

    def test_flag_unsupported_409_and_auto_disables(self, app, spam_api):
        client, token, account_id, cache_path, user_id = spam_api
        _seed(cache_path)
        _set_account_secret(app, account_id)
        msg_id = _message_id(client, token)
        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
            patch(
                "app.api.controllers.mail.report_spam",
                side_effect=SpamFlagUnsupportedError(),
            ),
        ):
            resp = client.post(f"/api/v1/mail/messages/{msg_id}/spam", headers=auth_header(token))
        assert resp.status_code == 409
        data = json.loads(resp.data)
        assert data["error"]["code"] == "SPAM_FLAG_UNSUPPORTED"
        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            assert settings is not None
            assert json.loads(settings.spam_action_prefs)[str(account_id)] is False


class TestNotSpam:
    def test_happy_path_from_junk_folder(self, app, spam_api):
        client, token, account_id, cache_path, _user_id = spam_api
        _seed(cache_path, folder="Junk")
        _set_account_secret(app, account_id)
        msg_id = _message_id(client, token, folder="Junk")
        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
            patch("app.api.controllers.mail.not_spam", return_value="INBOX") as mock_ns,
        ):
            resp = client.post(
                f"/api/v1/mail/messages/{msg_id}/not-spam", headers=auth_header(token)
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["id"] == msg_id
        assert data["moved_to"] == "INBOX"
        assert data["junk"] is False
        mock_ns.assert_called_once_with(mock_imap, "200", "Junk")

    def test_flag_only_outside_junk_folder(self, app, spam_api):
        client, token, account_id, cache_path, _user_id = spam_api
        _seed(cache_path, folder="INBOX", flags=["\\Junk", "\\Seen"])
        _set_account_secret(app, account_id)
        msg_id = _message_id(client, token)
        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
            patch("app.api.controllers.mail.not_spam", return_value=None) as mock_ns,
        ):
            resp = client.post(
                f"/api/v1/mail/messages/{msg_id}/not-spam", headers=auth_header(token)
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["moved_to"] is None
        assert data["junk"] is False
        mock_ns.assert_called_once_with(mock_imap, "200", "INBOX")

    def test_message_not_found(self, app, spam_api):
        client, token, _account_id, _cache_path, _user_id = spam_api
        resp = client.post("/api/v1/mail/messages/99999/not-spam", headers=auth_header(token))
        assert resp.status_code == 404

    def test_not_gated_by_spam_setting(self, app, spam_api):
        client, token, account_id, cache_path, user_id = spam_api
        _seed(cache_path, folder="Junk")
        _set_account_secret(app, account_id)
        _set_spam_pref(app, user_id, account_id, False)
        msg_id = _message_id(client, token, folder="Junk")
        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
            patch("app.api.controllers.mail.not_spam", return_value="INBOX"),
        ):
            resp = client.post(
                f"/api/v1/mail/messages/{msg_id}/not-spam", headers=auth_header(token)
            )
        assert resp.status_code == 200
