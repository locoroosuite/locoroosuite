import json
from unittest.mock import patch, MagicMock

import pytest

from tests.api.conftest import setup_cache_db, cleanup_cache_db, create_api_token, auth_header


@pytest.fixture()
def mail_api(app, api_customer):
    client, user_id, account_id = api_customer
    with app.app_context():
        token_value, _ = create_api_token(app, user_id)
    cache_path = setup_cache_db(app, account_id)
    yield client, token_value, account_id, cache_path
    cleanup_cache_db(cache_path)


def _set_account_secret(app, account_id, dek="a" * 64):
    from app.shared.db import db as _db
    from app.shared.models.core import CustomerAccount
    from app.modules.mail.services.secrets import encrypt_with_key
    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        assert account is not None
        account.encrypted_secret = encrypt_with_key("testpass", dek)
        _db.session.commit()


def _seed(cache_path, dek="a" * 64):
    from app.modules.mail.services.cache_db import open_cache, upsert_folder, upsert_message
    conn = open_cache(cache_path, dek)
    upsert_folder(conn, "INBOX", unread_count=1)
    upsert_folder(conn, "LifeLenz", unread_count=0)
    upsert_message(
        conn, uid="100", folder="INBOX",
        subject="LifeLenz intro", sender="a@example.com", recipients="b@example.com",
        date="Tue, 20 May 2026 10:00:00 +0000", flags=["\\Seen"],
        snippet="hi", body="hi", has_attachments=False,
        message_id="<m1@example.com>", thread_id="t1",
    )
    upsert_message(
        conn, uid="101", folder="INBOX",
        subject="Starred one", sender="c@example.com", recipients="b@example.com",
        date="Tue, 20 May 2026 11:00:00 +0000", flags=["\\Flagged"],
        snippet="starred", body="starred", has_attachments=False,
        message_id="<m2@example.com>", thread_id="t2",
    )
    conn.close()


class TestCreateFolder:
    def test_create_folder_happy_path(self, app, mail_api):
        client, token, account_id, _ = mail_api
        _set_account_secret(app, account_id)
        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Sent"]),
            patch("app.modules.mail.services.imap_client.create_folder", return_value=("OK", [b""])),
            patch("app.modules.mail.services.imap_client.get_folder_delimiter", return_value="/"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.post("/api/v1/mail/folders", json={"name": "LifeLenz"}, headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data == {"id": "LifeLenz", "name": "LifeLenz", "created": True}

    def test_create_folder_idempotent(self, app, mail_api):
        client, token, account_id, _ = mail_api
        _set_account_secret(app, account_id)
        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "LifeLenz"]),
            patch("app.modules.mail.services.imap_client.create_folder") as mock_create,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.post("/api/v1/mail/folders", json={"name": "LifeLenz"}, headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["created"] is False
        mock_create.assert_not_called()

    def test_create_folder_with_parent_uses_delimiter(self, app, mail_api):
        client, token, account_id, _ = mail_api
        _set_account_secret(app, account_id)
        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX"]),
            patch("app.modules.mail.services.imap_client.create_folder", return_value=("OK", [b""])) as mock_create,
            patch("app.modules.mail.services.imap_client.get_folder_delimiter", return_value="/"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.post(
                "/api/v1/mail/folders",
                json={"name": "Sub", "parent": "Work"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["name"] == "Work/Sub"
        mock_create.assert_called_once_with(mock_client, "Work/Sub")

    def test_create_folder_requires_name(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post("/api/v1/mail/folders", json={"name": ""}, headers=auth_header(token))
        assert resp.status_code == 400

    def test_create_folder_updates_cache_list(self, app, mail_api):
        client, token, account_id, _ = mail_api
        _set_account_secret(app, account_id)
        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX"]),
            patch("app.modules.mail.services.imap_client.create_folder", return_value=("OK", [b""])),
            patch("app.modules.mail.services.imap_client.get_folder_delimiter", return_value="/"),
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            client.post("/api/v1/mail/folders", json={"name": "LifeLenz"}, headers=auth_header(token))
        list_resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        names = {f["name"] for f in json.loads(list_resp.data)["data"]}
        assert "LifeLenz" in names


class TestRenameFolder:
    def test_rename_folder_happy_path(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        _seed(cache_path)
        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.rename_folder", return_value=("OK", [b""])) as mock_rename,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.post(
                "/api/v1/mail/folders/LifeLenz/rename",
                json={"name": "LifeLenz2"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data == {"id": "LifeLenz2", "name": "LifeLenz2"}
        mock_rename.assert_called_once_with(mock_client, "LifeLenz", "LifeLenz2")

    def test_rename_system_folder_refused(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/folders/INBOX/rename",
            json={"name": "Other"},
            headers=auth_header(token),
        )
        assert resp.status_code == 409
        assert json.loads(resp.data)["error"]["code"] == "PROTECTED"

    def test_rename_updates_cache(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        _seed(cache_path)
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=MagicMock()),
            patch("app.modules.mail.services.imap_client.rename_folder", return_value=("OK", [b""])),
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            client.post("/api/v1/mail/folders/LifeLenz/rename", json={"name": "Renamed"}, headers=auth_header(token))
        names = {f["name"] for f in json.loads(client.get("/api/v1/mail/folders", headers=auth_header(token)).data)["data"]}
        assert "Renamed" in names
        assert "LifeLenz" not in names


class TestDeleteFolder:
    def test_delete_folder_happy_path(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        _seed(cache_path)
        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.delete_folder", return_value=("OK", [b""])) as mock_delete,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.delete("/api/v1/mail/folders/LifeLenz", headers=auth_header(token))
        assert resp.status_code == 200
        assert json.loads(resp.data)["data"] == {"id": "LifeLenz", "deleted": True}
        mock_delete.assert_called_once_with(mock_client, "LifeLenz")

    def test_delete_system_folder_refused(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.delete("/api/v1/mail/folders/INBOX", headers=auth_header(token))
        assert resp.status_code == 409
        assert json.loads(resp.data)["error"]["code"] == "PROTECTED"

    def test_delete_user_protected_folder_refused(self, app, mail_api):
        client, token, account_id, _ = mail_api
        with app.app_context():
            from app.shared.db import db as _db
            from app.shared.models.core import CustomerAccount
            from app.modules.mail.controllers.helpers import _get_or_create_settings
            from app.modules.mail.services.protection import set_folder_protected
            account = _db.session.get(CustomerAccount, account_id)
            assert account is not None
            settings = _get_or_create_settings(account.customer_id)
            set_folder_protected(settings, "LifeLenz", True)
            _db.session.commit()
        resp = client.delete("/api/v1/mail/folders/LifeLenz", headers=auth_header(token))
        assert resp.status_code == 409


class TestLockFlag:
    def test_update_flags_lock_sets_locked_keyword(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        _seed(cache_path)
        msg_id = json.loads(client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token)).data)["data"][0]["id"]
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=MagicMock()),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.set_flag") as mock_set,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.patch(
                f"/api/v1/mail/messages/{msg_id}",
                json={"flags": {"locked": True}},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        stored = json.loads(json.loads(resp.data)["data"]["flags"])
        assert "$Locked" in stored
        locked_calls = [c for c in mock_set.call_args_list if c.args[2] == "$Locked"]
        assert len(locked_calls) == 1
        assert locked_calls[0].kwargs.get("add") is True

    def test_bulk_flag_lock(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed(cache_path)
        msg_id = json.loads(client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token)).data)["data"][0]["id"]
        resp = client.post(
            "/api/v1/mail/bulk/flag",
            json={"items": [{"message_id": msg_id, "flags": {"locked": True}}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        stored = json.loads(json.loads(client.get(f"/api/v1/mail/messages/{msg_id}", headers=auth_header(token)).data)["data"]["flags"])
        assert "$Locked" in stored

    def test_update_flags_unlock_removes_locked_keyword(self, app, mail_api):
        # The previously-untested unlock half: locked=False must call
        # set_flag with add=False and drop $Locked from the cache.
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        _seed(cache_path)
        msg_id = json.loads(client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token)).data)["data"][0]["id"]
        # first lock it so there is something to remove
        client.patch(f"/api/v1/mail/messages/{msg_id}", json={"flags": {"locked": True}}, headers=auth_header(token))
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=MagicMock()),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.set_flag") as mock_set,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.patch(
                f"/api/v1/mail/messages/{msg_id}",
                json={"flags": {"locked": False}},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        stored = json.loads(json.loads(resp.data)["data"]["flags"])
        assert "$Locked" not in stored
        locked_calls = [c for c in mock_set.call_args_list if c.args[2] == "$Locked"]
        assert len(locked_calls) == 1
        assert locked_calls[0].kwargs.get("add") is False


class TestDeleteProtection:
    def test_starred_message_refuses_delete(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed(cache_path)
        msgs = json.loads(client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token)).data)["data"]
        starred_id = next(m["id"] for m in msgs if m["flagged"])
        resp = client.delete(f"/api/v1/mail/messages/{starred_id}", headers=auth_header(token))
        assert resp.status_code == 409
        body = json.loads(resp.data)
        assert body["error"]["code"] == "PROTECTED"
        # HLD U5.15g: message must be specific and actionable (mention the active reason)
        assert "starred" in body["error"]["message"].lower()
        assert "unstar" in body["error"]["message"].lower()

    def test_locked_message_refuses_delete(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed(cache_path)
        msgs = json.loads(client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token)).data)["data"]
        plain_id = next(m["id"] for m in msgs if not m["flagged"])
        client.patch(f"/api/v1/mail/messages/{plain_id}", json={"flags": {"locked": True}}, headers=auth_header(token))
        resp = client.delete(f"/api/v1/mail/messages/{plain_id}", headers=auth_header(token))
        assert resp.status_code == 409
        body = json.loads(resp.data)
        assert body["error"]["code"] == "PROTECTED"
        assert "locked" in body["error"]["message"].lower()
        assert "unlock" in body["error"]["message"].lower()

    def test_bulk_delete_skips_protected(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        _seed(cache_path)
        msgs = json.loads(client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token)).data)["data"]
        starred_id = next(m["id"] for m in msgs if m["flagged"])
        plain_id = next(m["id"] for m in msgs if not m["flagged"])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=MagicMock()),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Trash"]),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.move_message"),
        ):
            resp = client.post(
                "/api/v1/mail/bulk/delete",
                json={"items": [{"message_id": starred_id}, {"message_id": plain_id}]},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        codes = [f["error"]["code"] for f in data["failed"]]
        assert "PROTECTED" in codes
        assert len(data["succeeded"]) == 1

    def test_move_to_trash_refuses_for_protected(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed(cache_path)
        msgs = json.loads(client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token)).data)["data"]
        starred_id = next(m["id"] for m in msgs if m["flagged"])
        resp = client.post(
            f"/api/v1/mail/messages/{starred_id}/move",
            json={"folder_id": "Trash"},
            headers=auth_header(token),
        )
        assert resp.status_code == 409
