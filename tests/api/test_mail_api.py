import json
from unittest.mock import patch, MagicMock, ANY

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


def _seed_mail_cache(cache_path, dek="a" * 64):
    from app.modules.mail.services.cache_db import open_cache, upsert_folder, upsert_message
    conn = open_cache(cache_path, dek)
    upsert_folder(conn, "INBOX", unread_count=2)
    upsert_folder(conn, "Sent", unread_count=0)
    upsert_message(
        conn, uid="100", folder="INBOX",
        subject="Chief Effectiveness Officer - Offer",
        sender="Alice <alice@example.com>",
        recipients="bob@example.com",
        date="Tue, 20 May 2026 10:00:00 +0000",
        flags=["\\Seen"],
        snippet="We are pleased to offer you the position...",
        body="We are pleased to offer you the position of Chief Effectiveness Officer.",
        has_attachments=False,
        message_id="<offer-001@example.com>",
        thread_id="offer-thread-001",
    )
    upsert_message(
        conn, uid="101", folder="INBOX",
        subject="Weekly standup notes",
        sender="Charlie <charlie@example.com>",
        recipients="bob@example.com",
        date="Tue, 20 May 2026 09:00:00 +0000",
        flags=[],
        snippet="Notes from this week's standup...",
        body="Notes from this week's standup meeting.",
        has_attachments=False,
        message_id="<standup-001@example.com>",
        thread_id="standup-thread-001",
    )
    conn.close()
    return conn


def _set_account_secret(app, account_id, dek="a" * 64):
    from app.shared.db import db as _db
    from app.shared.models.core import CustomerAccount
    from app.modules.mail.services.secrets import encrypt_with_key
    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        account.encrypted_secret = encrypt_with_key("testpass", dek)
        _db.session.commit()


class TestListFolders:
    def test_empty_state(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data["data"], list)
        assert data["data"] == []

    def test_returns_seeded_folders(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        names = {f["name"] for f in data["data"]}
        assert "INBOX" in names
        assert "Sent" in names

    def test_folder_has_unread_count(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        inbox = next(f for f in data["data"] if f["name"] == "INBOX")
        assert inbox["unread_count"] == 2

    def test_folder_id_equals_name(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for folder in data["data"]:
            assert "id" in folder
            assert "name" in folder
            assert folder["id"] == folder["name"]


class TestSearchMessages:
    def test_missing_query_returns_422(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.get("/api/v1/mail/search", headers=auth_header(token))
        assert resp.status_code == 422
        data = json.loads(resp.data)
        assert isinstance(data, list)
        assert data[0]["type"] == "missing"

    def test_empty_query_returns_400(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.get("/api/v1/mail/search?q=", headers=auth_header(token))
        assert resp.status_code == 400

    def test_search_returns_matching_messages(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/search?q=Chief+Effectiveness+Officer",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        msg = data["data"][0]
        assert msg["subject"] == "Chief Effectiveness Officer - Offer"
        assert msg["from"] == "Alice <alice@example.com>"
        assert msg["folder"] == "INBOX"
        assert "id" in msg
        assert "date" in msg
        assert "snippet" in msg
        assert isinstance(msg["unread"], bool)
        assert isinstance(msg["flagged"], bool)

    def test_search_no_results(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/search?q=nonexistent+xyzzy",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_search_response_has_pagination(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/search?q=standup", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "pagination" in data
        assert data["pagination"]["has_more"] is False


class TestListMessages:
    def test_empty_folder(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_returns_seeded_messages(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        subjects = {m["subject"] for m in data["data"]}
        assert "Chief Effectiveness Officer - Offer" in subjects
        assert "Weekly standup notes" in subjects

    def test_message_has_expected_fields(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        data = json.loads(resp.data)
        msg = data["data"][0]
        for key in ("id", "folder", "subject", "from", "to", "date", "flags", "snippet", "unread", "flagged"):
            assert key in msg, f"Missing field: {key}"


class TestGetMessage:
    def test_not_found(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.get("/api/v1/mail/messages/99999", headers=auth_header(token))
        assert resp.status_code == 404

    def test_returns_message_detail(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msg_id = json.loads(list_resp.data)["data"][0]["id"]

        resp = client.get(f"/api/v1/mail/messages/{msg_id}", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["id"] == msg_id
        assert "subject" in data
        assert "body_plain" in data

    def test_default_does_not_mark_read(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        unread_msg = next(m for m in msgs if m["unread"])
        msg_id = unread_msg["id"]

        resp = client.get(f"/api/v1/mail/messages/{msg_id}", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["unread"] is True

    def test_mark_read_true_marks_message_as_read(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        unread_msg = next(m for m in msgs if m["unread"])
        msg_id = unread_msg["id"]

        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.set_flag") as mock_set_flag,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.get(
                f"/api/v1/mail/messages/{msg_id}?mark_read=true",
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["unread"] is False
        mock_set_flag.assert_called_once_with(mock_imap, ANY, "\\Seen", add=True)

    def test_mark_read_on_already_read_message(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        read_msg = next(m for m in msgs if not m["unread"])
        msg_id = read_msg["id"]

        resp = client.get(
            f"/api/v1/mail/messages/{msg_id}?mark_read=true",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["unread"] is False

    def test_mark_read_imap_failure_still_succeeds(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        unread_msg = next(m for m in msgs if m["unread"])
        msg_id = unread_msg["id"]

        with patch("app.api.controllers.mail._imap_connect", side_effect=Exception("IMAP down")):
            resp = client.get(
                f"/api/v1/mail/messages/{msg_id}?mark_read=true",
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["unread"] is False

    def test_mark_read_not_found(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.get(
            "/api/v1/mail/messages/99999?mark_read=true",
            headers=auth_header(token),
        )
        assert resp.status_code == 404


class TestGetThread:
    def test_empty_thread(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.get("/api/v1/mail/threads/nonexistent-thread", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_returns_thread_messages(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/threads/offer-thread-001",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        assert data["data"][0]["subject"] == "Chief Effectiveness Officer - Offer"


class TestUpdateFlags:
    def test_not_found(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.patch(
            "/api/v1/mail/messages/99999",
            json={"flags": {"read": True}},
            headers=auth_header(token),
        )
        assert resp.status_code == 404

    def test_mark_as_read(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        unread_msg = next(m for m in msgs if m["unread"])
        msg_id = unread_msg["id"]

        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.set_flag") as mock_set_flag,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.patch(
                f"/api/v1/mail/messages/{msg_id}",
                json={"flags": {"read": True}},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "\\Seen" in data["flags"]
        mock_set_flag.assert_called_once_with(mock_imap, ANY, "\\Seen", add=True)

    def test_mark_as_unread(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        read_msg = next(m for m in msgs if not m["unread"])
        msg_id = read_msg["id"]

        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.set_flag") as mock_set_flag,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.patch(
                f"/api/v1/mail/messages/{msg_id}",
                json={"flags": {"read": False}},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "\\Seen" not in data["flags"]
        mock_set_flag.assert_called_once_with(mock_imap, ANY, "\\Seen", add=False)

    def test_mark_as_flagged_propagates_to_imap(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        msg = msgs[0]
        msg_id = msg["id"]

        mock_imap = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.set_flag") as mock_set_flag,
            patch("app.modules.mail.services.imap_client.safe_logout"),
        ):
            resp = client.patch(
                f"/api/v1/mail/messages/{msg_id}",
                json={"flags": {"flagged": True}},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "\\Flagged" in data["flags"]
        mock_set_flag.assert_called_once_with(mock_imap, ANY, "\\Flagged", add=True)

    def test_no_imap_call_when_no_flags_changed(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        read_msg = next(m for m in msgs if not m["unread"])
        msg_id = read_msg["id"]

        with patch("app.api.controllers.mail._imap_connect") as mock_connect:
            resp = client.patch(
                f"/api/v1/mail/messages/{msg_id}",
                json={"flags": {"read": True}},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        mock_connect.assert_not_called()

    def test_imap_failure_still_returns_success(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        unread_msg = next(m for m in msgs if m["unread"])
        msg_id = unread_msg["id"]

        with patch("app.api.controllers.mail._imap_connect", side_effect=Exception("IMAP down")):
            resp = client.patch(
                f"/api/v1/mail/messages/{msg_id}",
                json={"flags": {"read": True}},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "\\Seen" in data["flags"]


class TestBulkFlag:
    def test_empty_items_returns_400(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/flag",
            json={"items": []},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_bulk_flag_happy_path(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        msg_ids = [m["id"] for m in msgs]

        resp = client.post(
            "/api/v1/mail/bulk/flag",
            json={"items": [{"message_id": msg_ids[0], "flags": {"read": True, "flagged": True}}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["succeeded"]) == 1

        get_resp = client.get(f"/api/v1/mail/messages/{msg_ids[0]}", headers=auth_header(token))
        msg = json.loads(get_resp.data)["data"]
        assert msg["flagged"] is True
        assert msg["unread"] is False
        stored_flags = json.loads(msg["flags"])
        assert "\\Seen" in stored_flags
        assert "\\Flagged" in stored_flags

    def test_bulk_flag_unflag(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        msg_ids = [m["id"] for m in msgs]

        client.post(
            "/api/v1/mail/bulk/flag",
            json={"items": [{"message_id": msg_ids[0], "flags": {"flagged": True}}]},
            headers=auth_header(token),
        )
        resp = client.post(
            "/api/v1/mail/bulk/flag",
            json={"items": [{"message_id": msg_ids[0], "flags": {"flagged": False}}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        get_resp = client.get(f"/api/v1/mail/messages/{msg_ids[0]}", headers=auth_header(token))
        msg = json.loads(get_resp.data)["data"]
        assert msg["flagged"] is False
        stored_flags = json.loads(msg["flags"])
        assert "\\Flagged" not in stored_flags

    def test_bulk_flag_not_found(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/flag",
            json={"items": [{"message_id": 99999, "flags": {"read": True}}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["failed"]) == 1


class TestBulkDelete:
    def test_empty_items_returns_400(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/delete",
            json={"items": []},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_bulk_delete_not_found(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/delete",
            json={"items": [{"message_id": 99999}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"]["code"] == "NOT_FOUND"

    def test_bulk_delete_happy_path(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        msg_ids = [m["id"] for m in msgs]

        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Sent", "Trash"]),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.move_message"),
        ):
            resp = client.post(
                "/api/v1/mail/bulk/delete",
                json={"items": [{"message_id": msg_ids[0]}, {"message_id": msg_ids[1]}]},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["succeeded"]) == 2
        assert len(data["failed"]) == 0
        succeeded_ids = {s["message_id"] for s in data["succeeded"]}
        assert msg_ids[0] in succeeded_ids
        assert msg_ids[1] in succeeded_ids
        mock_client.expunge.assert_called_once()

    def test_bulk_delete_creates_trash_if_missing(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msg_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Sent"]),
            patch("app.modules.mail.services.imap_client.create_folder") as mock_create,
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.move_message"),
        ):
            resp = client.post(
                "/api/v1/mail/bulk/delete",
                json={"items": [{"message_id": msg_id}]},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        mock_create.assert_called_once_with(mock_client, "Trash")

    def test_bulk_delete_mix_found_and_not_found(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msg_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Trash"]),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.move_message"),
        ):
            resp = client.post(
                "/api/v1/mail/bulk/delete",
                json={"items": [{"message_id": msg_id}, {"message_id": 99999}]},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["succeeded"]) == 1
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"]["code"] == "NOT_FOUND"

    def test_bulk_delete_missing_message_id(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/delete",
            json={"items": [{}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"]["code"] == "MISSING_ID"


class TestDeleteMessage:
    def test_delete_message_removes_from_cache(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        msg_id = msgs[0]["id"]

        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Sent", "Trash"]),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.move_message"),
        ):
            resp = client.delete(f"/api/v1/mail/messages/{msg_id}", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["deleted"] is True
        mock_client.expunge.assert_called_once()

        get_resp = client.get(f"/api/v1/mail/messages/{msg_id}", headers=auth_header(token))
        assert get_resp.status_code == 404

    def test_delete_message_not_found(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.delete("/api/v1/mail/messages/99999", headers=auth_header(token))
        assert resp.status_code == 404

    def test_bulk_delete_removes_from_cache(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        msg_ids = [m["id"] for m in msgs]

        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Sent", "Trash"]),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.move_message"),
        ):
            resp = client.post(
                "/api/v1/mail/bulk/delete",
                json={"items": [{"message_id": msg_ids[0]}, {"message_id": msg_ids[1]}]},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["succeeded"]) == 2

        for mid in msg_ids:
            get_resp = client.get(f"/api/v1/mail/messages/{mid}", headers=auth_header(token))
            assert get_resp.status_code == 404


class TestBulkMove:
    def test_empty_items_returns_400(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/move",
            json={"items": [], "folder_id": "Archive"},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_missing_destination_returns_400(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/move",
            json={"items": [{"message_id": 1}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_bulk_move_happy_path(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        _set_account_secret(app, account_id)
        list_resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        msgs = json.loads(list_resp.data)["data"]
        msg_ids = [m["id"] for m in msgs]

        mock_client = MagicMock()
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_client),
            patch("app.modules.mail.services.imap_client.select_folder"),
            patch("app.modules.mail.services.imap_client.move_message"),
        ):
            resp = client.post(
                "/api/v1/mail/bulk/move",
                json={"items": [{"message_id": msg_ids[0]}], "folder_id": "Archive"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["succeeded"]) == 1
        assert data["succeeded"][0]["message_id"] == msg_ids[0]
        mock_client.expunge.assert_called_once()

    def test_bulk_move_not_found(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/move",
            json={"items": [{"message_id": 99999}], "folder_id": "Archive"},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"]["code"] == "NOT_FOUND"

    def test_bulk_move_missing_message_id(self, app, mail_api):
        client, token, account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/move",
            json={"items": [{}], "folder_id": "Archive"},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"]["code"] == "MISSING_ID"


class TestCreateDraft:
    def test_create_draft_happy_path(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b"[APPENDUID 1 42]"])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "Draft subject",
                    "body_plain": "Draft body",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["status"] == "draft"
        assert data["draft_uid"] == "42"
        assert data["message_id"] is not None
        app.sync_manager.enqueue_sync.assert_any_call(
            account_id, folder="Drafts", reason="draft_saved", priority=5,
        )

    def test_create_draft_with_html_body(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b"[APPENDUID 1 55]"])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["bob@example.com"],
                    "cc": ["carol@example.com"],
                    "bcc": ["dave@example.com"],
                    "subject": "HTML draft",
                    "body_html": "<p>Hello</p>",
                    "body_plain": "Hello",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["status"] == "draft"
        assert data["draft_uid"] == "55"

    def test_create_draft_creates_folder_if_missing(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.append.side_effect = [
            ("NO", [b""]),
            ("OK", [b"[APPENDUID 1 10]"]),
        ]

        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.create_folder") as mock_create,
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "Test",
                    "body_plain": "Body",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        mock_create.assert_called_once()

    def test_create_draft_replace_uid_deletes_old(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b"[APPENDUID 1 99]"])
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.uid.return_value = ("OK", [b""])

        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "Updated draft",
                    "body_plain": "Updated body",
                    "replace_uid": "42",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        mock_del.assert_called_once_with(mock_imap, "42")

    def test_create_draft_no_body_still_saves(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b"[APPENDUID 1 30]"])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "Empty draft",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201

    def test_create_draft_string_to_field(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b"[APPENDUID 1 31]"])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": "single@example.com",
                    "subject": "String to",
                    "body_plain": "Body",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201

    def test_create_draft_imap_failure(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        with (
            patch("app.api.controllers.mail._imap_connect", side_effect=Exception("IMAP down")),
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "Fail",
                    "body_plain": "Body",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 500

    def test_save_draft_returns_draft_id_and_uid(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b"[APPENDUID 1 77]"])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "ID Check",
                    "body_plain": "Body",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert "draft_id" in data
        assert "draft_uid" in data
        assert data["draft_id"] == "77"
        assert data["draft_uid"] == "77"
        assert data["status"] == "draft"
        assert "message_id" in data


class TestDeleteDraft:
    def test_delete_draft_happy_path(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.uid.return_value = ("OK", [b""])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
        ):
            resp = client.delete(
                "/api/v1/mail/drafts/42",
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["status"] == "deleted"
        assert data["draft_uid"] == "42"
        mock_del.assert_called_once_with(mock_imap, "42")

    def test_delete_draft_imap_failure(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        with (
            patch("app.api.controllers.mail._imap_connect", side_effect=Exception("IMAP down")),
        ):
            resp = client.delete(
                "/api/v1/mail/drafts/99",
                headers=auth_header(token),
            )
        assert resp.status_code == 500

    def test_delete_draft_returns_draft_id(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.uid.return_value = ("OK", [b""])
        with (
            patch("app.api.controllers.mail._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.delete_message_by_uid"),
        ):
            resp = client.delete(
                "/api/v1/mail/drafts/88",
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "draft_id" in data
        assert data["draft_id"] == "88"
        assert data["draft_uid"] == "88"
        assert data["status"] == "deleted"


class TestSendWithDraftCleanup:
    def test_send_with_draft_uid_deletes_draft(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_smtp = MagicMock()
        mock_imap_sent = MagicMock()
        mock_imap_sent.append.return_value = ("OK", [b""])
        mock_imap_draft = MagicMock()
        mock_imap_draft.select.return_value = ("OK", [b"1"])
        mock_imap_draft.uid.return_value = ("OK", [b""])

        imap_connect_calls = [mock_imap_sent, mock_imap_draft]

        with (
            patch("app.modules.mail.services.smtp_client.smtp_connect", return_value=mock_smtp),
            patch("app.modules.mail.services.smtp_client.smtp_login"),
            patch("app.modules.mail.services.smtp_client.smtp_send"),
            patch("app.modules.mail.services.send._imap_connect", side_effect=imap_connect_calls),
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
            patch("app.modules.mail.services.cache_db.delete_messages_by_uids") as mock_cache_del,
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["bob@example.com"],
                    "subject": "Sent from draft",
                    "body_plain": "Hello",
                    "draft_uid": "42",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"
        mock_del.assert_called_once_with(mock_imap_draft, "42")
        mock_cache_del.assert_called_once()
        args = mock_cache_del.call_args[0]
        assert args[1] == "Drafts"
        assert args[2] == ["42"]
        app.sync_manager.enqueue_sync.assert_any_call(
            account_id, folder="Sent", reason="send_complete", priority=5,
        )
        app.sync_manager.enqueue_sync.assert_any_call(
            account_id, folder="Drafts", reason="send_complete", priority=5,
        )

    def test_send_without_draft_uid_skips_cleanup(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_smtp = MagicMock()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])

        with (
            patch("app.modules.mail.services.smtp_client.smtp_connect", return_value=mock_smtp),
            patch("app.modules.mail.services.smtp_client.smtp_login"),
            patch("app.modules.mail.services.smtp_client.smtp_send"),
            patch("app.modules.mail.services.send._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["bob@example.com"],
                    "subject": "No draft",
                    "body_plain": "Hello",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        assert mock_imap.uid.call_count == 0

    def test_send_draft_cleanup_failure_does_not_affect_send(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_smtp = MagicMock()
        mock_imap_sent = MagicMock()
        mock_imap_sent.append.return_value = ("OK", [b""])

        def _imap_connect_side_effect(*args, **kwargs):
            if _imap_connect_side_effect.call_count == 0:
                _imap_connect_side_effect.call_count += 1
                return mock_imap_sent
            raise Exception("IMAP down for draft cleanup")

        _imap_connect_side_effect.call_count = 0

        with (
            patch("app.modules.mail.services.smtp_client.smtp_connect", return_value=mock_smtp),
            patch("app.modules.mail.services.smtp_client.smtp_login"),
            patch("app.modules.mail.services.smtp_client.smtp_send"),
            patch("app.modules.mail.services.send._imap_connect", side_effect=_imap_connect_side_effect),
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["bob@example.com"],
                    "subject": "Draft cleanup fails",
                    "body_plain": "Hello",
                    "draft_uid": "99",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"

    def test_send_returns_full_object(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_smtp = MagicMock()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])

        with (
            patch("app.modules.mail.services.smtp_client.smtp_connect", return_value=mock_smtp),
            patch("app.modules.mail.services.smtp_client.smtp_login"),
            patch("app.modules.mail.services.smtp_client.smtp_send"),
            patch("app.modules.mail.services.send._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["bob@example.com"],
                    "subject": "Contract Test",
                    "body_plain": "Hello",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"
        assert "message_id" in data
        assert data["message_id"] is not None
        assert data["subject"] == "Contract Test"
        assert data["from"] == "api@example.com"
        assert data["to"] == ["bob@example.com"]
        assert isinstance(data["cc"], list)
