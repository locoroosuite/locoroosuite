import json
from unittest.mock import ANY, MagicMock, patch

import pytest

from tests.api.conftest import auth_header, cleanup_cache_db, create_api_token, setup_cache_db


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
        conn,
        uid="100",
        folder="INBOX",
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
        conn,
        uid="101",
        folder="INBOX",
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
    from app.modules.mail.services.secrets import encrypt_with_key
    from app.shared.db import db as _db
    from app.shared.models.core import CustomerAccount

    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        assert account is not None
        account.encrypted_secret = encrypt_with_key("testpass", dek)
        _db.session.commit()


class TestListFolders:
    def test_empty_state(self, app, mail_api):
        client, token, _account_id, _ = mail_api
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data["data"], list)
        assert data["data"] == []

    def test_returns_seeded_folders(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        names = {f["name"] for f in data["data"]}
        assert "INBOX" in names
        assert "Sent" in names

    def test_folder_has_unread_count(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        inbox = next(f for f in data["data"] if f["name"] == "INBOX")
        assert inbox["unread_count"] == 2

    def test_folder_id_equals_name(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for folder in data["data"]:
            assert "id" in folder
            assert "name" in folder
            assert folder["id"] == folder["name"]


class TestSearchMessages:
    def test_no_criteria_returns_400(self, app, mail_api):
        """U15.34: q is optional when filters are used, but a request with no
        criteria at all is a validation error."""
        client, token, _account_id, _ = mail_api
        resp = client.get("/api/v1/mail/search", headers=auth_header(token))
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_empty_query_returns_400(self, app, mail_api):
        client, token, _account_id, _ = mail_api
        resp = client.get("/api/v1/mail/search?q=", headers=auth_header(token))
        assert resp.status_code == 400

    def test_search_response_includes_total_count(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/search?q=standup", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_count"] == 1

    def test_search_operator_from(self, app, mail_api):
        """U15.34/U7.4: operators inside q work end-to-end."""
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/search?q=from%3Aalice", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_count"] == 1
        assert data["data"][0]["subject"] == "Chief Effectiveness Officer - Offer"

    def test_search_filter_unread_only(self, app, mail_api):
        """U15.34: the `unread` filter is now honored (was silently ignored)."""
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/search?q=standup&unread=true", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_count"] == 1
        assert data["data"][0]["unread"] is True

    def test_search_filter_folder_id(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/search?q=offer&folder_id=Sent", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_count"] == 0

    def test_search_filters_only_without_q(self, app, mail_api):
        """Filters alone (no free-text q) are a valid query."""
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/search?unread=true", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_count"] == 1
        assert data["data"][0]["subject"] == "Weekly standup notes"

    def test_search_returns_matching_messages(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/search?q=nonexistent+xyzzy",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_search_response_has_pagination(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/search?q=standup", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "pagination" in data
        assert data["pagination"]["has_more"] is False


class TestListMessages:
    def test_empty_folder(self, app, mail_api):
        client, token, _account_id, _ = mail_api
        resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_returns_seeded_messages(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        subjects = {m["subject"] for m in data["data"]}
        assert "Chief Effectiveness Officer - Offer" in subjects
        assert "Weekly standup notes" in subjects

    def test_message_has_expected_fields(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get("/api/v1/mail/folders/INBOX/messages", headers=auth_header(token))
        data = json.loads(resp.data)
        msg = data["data"][0]
        for key in (
            "id",
            "folder",
            "subject",
            "from",
            "to",
            "date",
            "flags",
            "snippet",
            "unread",
            "flagged",
        ):
            assert key in msg, f"Missing field: {key}"


class TestGetMessage:
    def test_not_found(self, app, mail_api):
        client, token, _account_id, _ = mail_api
        resp = client.get("/api/v1/mail/messages/99999", headers=auth_header(token))
        assert resp.status_code == 404

    def test_returns_message_detail(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, _ = mail_api
        resp = client.get(
            "/api/v1/mail/messages/99999?mark_read=true",
            headers=auth_header(token),
        )
        assert resp.status_code == 404


class TestGetThread:
    def test_empty_thread(self, app, mail_api):
        client, token, _account_id, _ = mail_api
        resp = client.get("/api/v1/mail/threads/nonexistent-thread", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_returns_thread_messages(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, _ = mail_api
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
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/flag",
            json={"items": []},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_bulk_flag_happy_path(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, cache_path = mail_api
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
        client, token, _account_id, _ = mail_api
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
        client, token, _account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/delete",
            json={"items": []},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_bulk_delete_not_found(self, app, mail_api):
        client, token, _account_id, _ = mail_api
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
            patch(
                "app.modules.mail.services.imap_client.list_folders",
                return_value=["INBOX", "Sent", "Trash"],
            ),
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
            patch(
                "app.modules.mail.services.imap_client.list_folders", return_value=["INBOX", "Sent"]
            ),
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
            patch(
                "app.modules.mail.services.imap_client.list_folders",
                return_value=["INBOX", "Trash"],
            ),
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
        client, token, _account_id, _ = mail_api
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
            patch(
                "app.modules.mail.services.imap_client.list_folders",
                return_value=["INBOX", "Sent", "Trash"],
            ),
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
        client, token, _account_id, _ = mail_api
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
            patch(
                "app.modules.mail.services.imap_client.list_folders",
                return_value=["INBOX", "Sent", "Trash"],
            ),
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
        client, token, _account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/move",
            json={"items": [], "folder_id": "Archive"},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_missing_destination_returns_400(self, app, mail_api):
        client, token, _account_id, _ = mail_api
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
        client, token, _account_id, _ = mail_api
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
        client, token, _account_id, _ = mail_api
        resp = client.post(
            "/api/v1/mail/bulk/move",
            json={"items": [{}], "folder_id": "Archive"},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"]["code"] == "MISSING_ID"


class TestDraftRouteAuth:
    """AGENTS testing rules: every changed route also covers unauthorized."""

    def test_create_draft_requires_token(self, app, mail_api):
        client = mail_api[0]
        resp = client.post("/api/v1/mail/drafts", json={"to": ["a@example.com"], "subject": "x"})
        assert resp.status_code == 401
        body = json.loads(resp.data)
        assert body["error"]["code"]

    def test_delete_draft_requires_token(self, app, mail_api):
        client = mail_api[0]
        resp = client.delete("/api/v1/mail/drafts/42")
        assert resp.status_code == 401

    def test_send_requires_token(self, app, mail_api):
        client = mail_api[0]
        resp = client.post(
            "/api/v1/mail/messages",
            json={"to": ["a@example.com"], "subject": "x", "body_plain": "y"},
        )
        assert resp.status_code == 401

    def test_thread_requires_token(self, app, mail_api):
        client = mail_api[0]
        resp = client.get("/api/v1/mail/threads/some-thread")
        assert resp.status_code == 401


class TestCreateDraft:
    r"""U6.4/U6.5a-c: draft create/replace via the shared drafts service.

    Repro coverage for the 2026-11-14 MCP issue log:
    - #1 replace_uid silently ignored -> replaced by either ID space, or 404
    - #5 ID spaces unified -> cache id accepted everywhere
    - #9 drafts stored read (\Draft \Seen)
    - #3 structured errors instead of raw 500
    """

    def _imap_mock(self, append_uid="42", search_results=None):
        mock = MagicMock()
        mock.append.return_value = ("OK", [f"[APPENDUID 1 {append_uid}]".encode()])
        mock.select.return_value = ("OK", [b"1"])
        searches = search_results or {}

        def _uid(cmd, *args):
            if cmd == "SEARCH":
                crit = args[-1] if args else ""
                hits = searches.get(crit, b"")
                if isinstance(hits, list):
                    return ("OK", [hits.pop(0)])
                return ("OK", [hits])
            return ("OK", [b""])

        mock.uid.side_effect = _uid
        return mock

    def _draft_imap_patches(self, mock):
        return (
            patch("app.modules.mail.services.imap_client.connect_imap", return_value=mock),
            patch("app.modules.mail.services.imap_client.login_imap"),
            patch(
                "app.modules.mail.services.imap_client.fetch_message_with_flags",
                return_value=(None, [], None),
            ),
        )

    def test_create_draft_happy_path(self, app, mail_api):
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock(append_uid="42")
        imap_patches = self._draft_imap_patches(mock_imap)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
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
        # U6.4/#9: drafts are stored read — \Draft plus \Seen.
        flags_arg = mock_imap.append.call_args[0][1]
        assert flags_arg == "(\\Draft \\Seen)"
        app.sync_manager.enqueue_sync.assert_any_call(
            account_id,
            folder="Drafts",
            reason="draft_saved",
            priority=5,
        )

    def test_create_draft_with_html_body(self, app, mail_api):
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock(append_uid="55")
        imap_patches = self._draft_imap_patches(mock_imap)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
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
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock(append_uid="10")
        import imaplib

        mock_imap.append.side_effect = [
            imaplib.IMAP4.error("no folder"),
            imaplib.IMAP4.error("still no folder"),
            ("OK", [b"[APPENDUID 1 10]"]),
        ]
        draft_patches = self._draft_imap_patches(mock_imap)
        with (
            draft_patches[0],
            draft_patches[1],
            draft_patches[2],
            patch("app.modules.mail.services.imap_client.list_folders", return_value=["INBOX"]),
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

    def test_create_draft_replace_by_cache_message_id(self, app, mail_api):
        """Issue #1/#5 repro: replace_uid accepts the cache message id (the id
        read endpoints expose) and replaces the resolved draft, no duplicate."""
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        from app.modules.mail.services.cache_db import open_cache, upsert_message

        conn = open_cache(cache_path, "a" * 64)
        upsert_message(
            conn,
            uid="42",
            folder="Drafts",
            subject="Old draft",
            sender="api@example.com",
            recipients="alice@example.com",
            date="Tue, 20 May 2026 10:00:00 +0000",
            flags=["\\Draft", "\\Seen"],
            snippet="old",
            body="old body",
            has_attachments=False,
            message_id="<old-draft@example.com>",
            thread_id=None,
        )
        conn.close()

        mock_imap = self._imap_mock(append_uid="99", search_results={"UID 42": [b""]})
        draft_patches = self._draft_imap_patches(mock_imap)
        with (
            draft_patches[0],
            draft_patches[1],
            draft_patches[2],
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "Updated draft",
                    "body_plain": "Updated body",
                    # the CACHE id of the seeded draft row, not its IMAP uid
                    "replace_uid": "1",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["draft_uid"] == "99"
        # resolved to the IMAP uid before deletion (U6.5b)
        mock_del.assert_called_once_with(mock_imap, "42")

    def test_create_draft_replace_unknown_returns_404_no_duplicate(self, app, mail_api):
        """Issue #1 repro: an unknown replace identifier must be a loud 404 —
        never a silent duplicate append."""
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock(append_uid="99", search_results={"UID 1194": [b""]})
        imap_patches = self._draft_imap_patches(mock_imap)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
        ):
            resp = client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["alice@example.com"],
                    "subject": "Should not duplicate",
                    "body_plain": "Body",
                    "replace_uid": "1194",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert data["error"]["code"] == "DRAFT_NOT_FOUND"
        mock_imap.append.assert_not_called()

    def test_create_draft_no_body_still_saves(self, app, mail_api):
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock(append_uid="30")
        imap_patches = self._draft_imap_patches(mock_imap)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
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
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock(append_uid="31")
        imap_patches = self._draft_imap_patches(mock_imap)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
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

    def test_create_draft_imap_failure_structured_error(self, app, mail_api):
        """Issue #3 repro: IMAP failures return a structured 502 — never a
        raw HTML 500 page."""
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        with patch(
            "app.modules.mail.services.imap_client.connect_imap",
            side_effect=Exception("IMAP down"),
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
        assert resp.status_code == 502
        data = json.loads(resp.data)
        assert data["error"]["code"] == "MAILBOX_UNAVAILABLE"

    def test_save_draft_returns_draft_id_and_uid(self, app, mail_api):
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock(append_uid="77")
        imap_patches = self._draft_imap_patches(mock_imap)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
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
    """U6.5b/c: honest draft deletion — verified, by either ID space.

    Repro coverage: #2 (phantom success), #3 (intermittent 500), #5 (ID spaces).
    """

    def _imap_mock(self, search_results=None):
        mock = MagicMock()
        mock.select.return_value = ("OK", [b"1"])
        searches = search_results or {}

        def _uid(cmd, *args):
            if cmd == "SEARCH":
                crit = args[-1] if args else ""
                hits = searches.get(crit, b"")
                if isinstance(hits, list):
                    return ("OK", [hits.pop(0)])
                return ("OK", [hits])
            return ("OK", [b""])

        mock.uid.side_effect = _uid
        return mock

    def _patches(self, mock):
        return (
            patch("app.modules.mail.services.imap_client.connect_imap", return_value=mock),
            patch("app.modules.mail.services.imap_client.login_imap"),
        )

    def test_delete_draft_happy_path_verified(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        from app.modules.mail.services.cache_db import (
            get_message_by_uid_and_folder,
            open_cache,
            upsert_message,
        )

        conn = open_cache(cache_path, "a" * 64)
        upsert_message(
            conn,
            uid="42",
            folder="Drafts",
            subject="Doomed draft",
            sender="api@example.com",
            recipients="alice@example.com",
            date="Tue, 20 May 2026 10:00:00 +0000",
            flags=["\\Draft", "\\Seen"],
            snippet="x",
            body="x",
            has_attachments=False,
            message_id="<doomed@example.com>",
        )
        seeded = get_message_by_uid_and_folder(conn, "42", "Drafts")
        assert seeded is not None
        row_id = seeded["id"]
        conn.close()

        # UID 42 exists before the delete, gone after (verification, U6.5c)
        mock_imap = self._imap_mock({"UID 42": [b"42", b""]})
        del_patches = self._patches(mock_imap)
        with (
            del_patches[0],
            del_patches[1],
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
        assert data["draft_id"] == row_id
        mock_del.assert_called_once_with(mock_imap, "42")
        # cache row is gone too — a listing right after never shows the draft (#2b)
        conn = open_cache(cache_path, "a" * 64)
        assert get_message_by_uid_and_folder(conn, "42", "Drafts") is None
        conn.close()

    def test_delete_draft_unknown_uid_returns_404(self, app, mail_api):
        """Issue #2a repro: deleting a nonexistent draft must error, not
        report success."""
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_imap = self._imap_mock({"UID 343": [b""]})
        imap_patches = self._patches(mock_imap)
        with (
            imap_patches[0],
            imap_patches[1],
        ):
            resp = client.delete(
                "/api/v1/mail/drafts/343",
                headers=auth_header(token),
            )
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert data["error"]["code"] == "DRAFT_NOT_FOUND"

    def test_delete_draft_by_cache_message_id(self, app, mail_api):
        """Issue #2b/#5 repro: the cache message id is accepted and resolved
        to the real IMAP uid before deletion."""
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        from app.modules.mail.services.cache_db import (
            get_message_by_uid_and_folder,
            open_cache,
            upsert_message,
        )

        conn = open_cache(cache_path, "a" * 64)
        upsert_message(
            conn,
            uid="343",
            folder="Drafts",
            subject="1195 draft",
            sender="api@example.com",
            recipients="alice@example.com",
            date="Tue, 20 May 2026 10:00:00 +0000",
            flags=["\\Draft", "\\Seen"],
            snippet="x",
            body="x",
            has_attachments=False,
            message_id="<d1195@example.com>",
        )
        seeded = get_message_by_uid_and_folder(conn, "343", "Drafts")
        assert seeded is not None
        row_id = seeded["id"]
        conn.close()

        mock_imap = self._imap_mock({"UID 343": [b"343", b""]})
        del_patches = self._patches(mock_imap)
        with (
            del_patches[0],
            del_patches[1],
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
        ):
            resp = client.delete(
                f"/api/v1/mail/drafts/{row_id}",
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["status"] == "deleted"
        assert data["draft_uid"] == "343"
        mock_del.assert_called_once_with(mock_imap, "343")
        conn = open_cache(cache_path, "a" * 64)
        assert get_message_by_uid_and_folder(conn, "343", "Drafts") is None
        conn.close()

    def test_delete_draft_still_present_returns_502(self, app, mail_api):
        """U6.5c: if the UID survives the delete, report failure loudly."""
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        from app.modules.mail.services.cache_db import open_cache, upsert_message

        conn = open_cache(cache_path, "a" * 64)
        upsert_message(
            conn,
            uid="50",
            folder="Drafts",
            subject="Sticky draft",
            sender="api@example.com",
            recipients="a@example.com",
            date="Tue, 20 May 2026 10:00:00 +0000",
            flags=["\\Draft"],
            snippet="x",
            body="x",
            has_attachments=False,
            message_id="<sticky@example.com>",
        )
        conn.close()

        # exists before AND after the delete — server ignored the STORE/EXPUNGE
        mock_imap = self._imap_mock({"UID 50": [b"50", b"50"]})
        del_patches = self._patches(mock_imap)
        with (
            del_patches[0],
            del_patches[1],
            patch("app.modules.mail.services.imap_client.delete_message_by_uid"),
        ):
            resp = client.delete(
                "/api/v1/mail/drafts/50",
                headers=auth_header(token),
            )
        assert resp.status_code == 502
        data = json.loads(resp.data)
        assert data["error"]["code"] == "DRAFT_DELETE_FAILED"

    def test_delete_draft_imap_failure_structured_error(self, app, mail_api):
        """Issue #3 repro: IMAP failure is a structured 502, never raw HTML."""
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        from app.modules.mail.services.cache_db import open_cache, upsert_message

        conn = open_cache(cache_path, "a" * 64)
        upsert_message(
            conn,
            uid="99",
            folder="Drafts",
            subject="Cache hit, server down",
            sender="api@example.com",
            recipients="a@example.com",
            date="Tue, 20 May 2026 10:00:00 +0000",
            flags=["\\Draft", "\\Seen"],
            snippet="x",
            body="x",
            has_attachments=False,
            message_id="<down@example.com>",
        )
        conn.close()

        with patch(
            "app.modules.mail.services.imap_client.connect_imap",
            side_effect=Exception("IMAP down"),
        ):
            resp = client.delete(
                "/api/v1/mail/drafts/99",
                headers=auth_header(token),
            )
        assert resp.status_code == 502
        data = json.loads(resp.data)
        assert data["error"]["code"] == "MAILBOX_UNAVAILABLE"

    def test_delete_unknown_uid_without_cache_row_returns_404(self, app, mail_api):
        """Cache-only resolution (U6.5b): an identifier with no cache row is
        404 without any IMAP round-trip."""
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)
        with patch("app.modules.mail.services.imap_client.connect_imap") as mock_connect:
            resp = client.delete(
                "/api/v1/mail/drafts/343",
                headers=auth_header(token),
            )
        assert resp.status_code == 404
        mock_connect.assert_not_called()


class TestSendWithDraftCleanup:
    """U15.26/U6.5c: send + delete-after-send with honest reporting (#4, #5)."""

    def _smtp_patches(self):
        mock_smtp = MagicMock()
        return (
            mock_smtp,
            patch("app.modules.mail.services.smtp_client.smtp_connect", return_value=mock_smtp),
            patch("app.modules.mail.services.smtp_client.smtp_login"),
            patch("app.modules.mail.services.smtp_client.smtp_send"),
        )

    def _seed_draft(self, cache_path, uid="42"):
        from app.modules.mail.services.cache_db import open_cache, upsert_message

        conn = open_cache(cache_path, "a" * 64)
        upsert_message(
            conn,
            uid=uid,
            folder="Drafts",
            subject="Draft to send",
            sender="api@example.com",
            recipients="bob@example.com",
            date="Tue, 20 May 2026 10:00:00 +0000",
            flags=["\\Draft", "\\Seen"],
            snippet="x",
            body="x",
            has_attachments=False,
            message_id=f"<draft-{uid}@example.com>",
        )
        conn.close()

    def test_send_with_draft_uid_deletes_draft_and_reports_it(self, app, mail_api):
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        self._seed_draft(cache_path, uid="42")

        _mock_smtp, *smtp_patches = self._smtp_patches()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])
        mock_imap.select.return_value = ("OK", [b"1"])
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch("app.modules.mail.services.send._imap_connect", return_value=mock_imap),
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
        # U6.5c(c): the delete-after-send outcome is reported, not swallowed
        assert data["draft_deleted"] is True
        mock_del.assert_called_once_with(mock_imap, "42")
        mock_cache_del.assert_called_once()
        args = mock_cache_del.call_args[0]
        assert args[1] == "Drafts"
        assert args[2] == ["42"]
        app.sync_manager.enqueue_sync.assert_any_call(
            account_id,
            folder="Sent",
            reason="send_complete",
            priority=5,
        )
        app.sync_manager.enqueue_sync.assert_any_call(
            account_id,
            folder="Drafts",
            reason="send_complete",
            priority=5,
        )

    def test_send_with_draft_cache_id_resolves_and_deletes(self, app, mail_api):
        """Issue #4/#5 repro: passing the message id (what read endpoints
        expose) deletes the right draft."""
        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        self._seed_draft(cache_path, uid="42")
        from app.modules.mail.services.cache_db import get_message_by_uid_and_folder, open_cache

        conn = open_cache(cache_path, "a" * 64)
        seeded = get_message_by_uid_and_folder(conn, "42", "Drafts")
        assert seeded is not None
        row_id = seeded["id"]
        conn.close()

        _mock_smtp, *smtp_patches = self._smtp_patches()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])
        mock_imap.select.return_value = ("OK", [b"1"])
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch("app.modules.mail.services.send._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["bob@example.com"],
                    "subject": "Sent from draft by cache id",
                    "body_plain": "Hello",
                    "draft_id": str(row_id),
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["draft_deleted"] is True
        mock_del.assert_called_once_with(mock_imap, "42")

    def test_send_with_unknown_draft_reports_not_deleted(self, app, mail_api):
        """Issue #4 repro: an unresolvable draft id never fails the send (the
        mail is already out) but is reported honestly."""
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        _mock_smtp, *smtp_patches = self._smtp_patches()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])
        mock_imap.select.return_value = ("OK", [b"1"])
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch("app.modules.mail.services.send._imap_connect", return_value=mock_imap),
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["bob@example.com"],
                    "subject": "Unknown draft ref",
                    "body_plain": "Hello",
                    "draft_id": "1195",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"
        assert data["draft_deleted"] is False
        assert data["draft_error"] == "DRAFT_NOT_FOUND"
        mock_del.assert_not_called()

    def test_send_without_draft_uid_skips_cleanup(self, app, mail_api):
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        _mock_smtp, *smtp_patches = self._smtp_patches()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])

        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
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
        self._seed_draft(cache_path, uid="99")

        _mock_smtp, *smtp_patches = self._smtp_patches()
        mock_imap_sent = MagicMock()
        mock_imap_sent.append.return_value = ("OK", [b""])

        imap_calls = [0]

        def _imap_connect_side_effect(*args, **kwargs):
            if imap_calls[0] == 0:
                imap_calls[0] += 1
                return mock_imap_sent
            raise Exception("IMAP down for draft cleanup")

        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch(
                "app.modules.mail.services.send._imap_connect",
                side_effect=_imap_connect_side_effect,
            ),
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
        assert data["draft_deleted"] is False
        assert data["draft_error"] == "DRAFT_DELETE_FAILED"

    def test_send_returns_full_object(self, app, mail_api):
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        _mock_smtp, *smtp_patches = self._smtp_patches()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])

        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
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


class TestDraftSavedEventPayload:
    """U27.7: draft_saved ui_change events carry enough context (draft_uid,
    message_id, thread_id, snippet) for open views to decide relevance."""

    def test_draft_saved_event_carries_context(self, app, mail_api):
        from app.shared import events

        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)
        captured: list = []

        def _rec(user_id, event_type, data):
            if event_type == "ui_change" and data.get("action") == "draft_saved":
                captured.append((user_id, data))

        events._subscribers.append(_rec)
        try:
            mock = MagicMock()
            mock.append.return_value = ("OK", [b"[APPENDUID 1 71]"])
            mock.select.return_value = ("OK", [b"1"])

            def _uid(cmd, *args):
                return ("OK", [b""])

            mock.uid.side_effect = _uid
            with (
                patch(
                    "app.modules.mail.services.imap_client.connect_imap",
                    return_value=mock,
                ),
                patch("app.modules.mail.services.imap_client.login_imap"),
                patch(
                    "app.modules.mail.services.imap_client.fetch_message_with_flags",
                    return_value=(None, [], None),
                ),
            ):
                resp = client.post(
                    "/api/v1/mail/drafts",
                    json={"to": ["a@example.com"], "subject": "evt", "body_plain": "b"},
                    headers=auth_header(token),
                )
            assert resp.status_code == 201
        finally:
            events._subscribers.remove(_rec)
        assert captured, "draft_saved event missing"
        _user_id, data = captured[-1]
        assert data.get("account_id") == account_id
        assert data.get("draft_uid") == "71"
        assert "message_id" in data
        assert "thread_id" in data
        assert "snippet" in data


class TestSendReplyThreading:
    """U15.26: reply threading headers (#6)."""

    def _smtp_patches(self):
        mock_smtp = MagicMock()
        mock_send = MagicMock()
        return (
            mock_send,
            patch("app.modules.mail.services.smtp_client.smtp_connect", return_value=mock_smtp),
            patch("app.modules.mail.services.smtp_client.smtp_login"),
            patch("app.modules.mail.services.smtp_client.smtp_send", mock_send),
        )

    def _seed_parent(self, cache_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message

        conn = open_cache(cache_path, "a" * 64)
        upsert_message(
            conn,
            uid="200",
            folder="INBOX",
            subject="NEW ROLE - Software Development Manager",
            sender="Gemma <gemma@example.com>",
            recipients="ruben@example.com",
            date="Sun, 26 Oct 2026 09:00:00 +0000",
            flags=["\\Seen"],
            snippet="role",
            body="role body",
            has_attachments=False,
            message_id="<179248033325.8@example.com>",
            thread_id="179248033325.8@example.com",
            in_reply_to=None,
            ref_list=None,
        )
        found = conn.execute(
            "SELECT id FROM messages WHERE uid = '200' AND folder = 'INBOX'"
        ).fetchone()
        assert found is not None
        row_id = found["id"]
        conn.close()
        return row_id

    def test_send_with_in_reply_to_sets_threading_headers(self, app, mail_api):
        import email as _email

        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_send, *smtp_patches = self._smtp_patches()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])
        mock_imap.select.return_value = ("OK", [b"1"])
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch("app.modules.mail.services.send._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["gemma@example.com"],
                    "subject": "Re: NEW ROLE",
                    "body_plain": "Reply body",
                    "in_reply_to": "<179248033325.8@example.com>",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        sent_bytes = mock_send.call_args[0][3]
        msg = _email.message_from_bytes(sent_bytes)
        assert msg["In-Reply-To"] == "<179248033325.8@example.com>"
        assert "<179248033325.8@example.com>" in (msg["References"] or "")

    def test_send_with_reply_to_message_id_derives_references(self, app, mail_api):
        import email as _email

        client, token, account_id, cache_path = mail_api
        _set_account_secret(app, account_id)
        row_id = self._seed_parent(cache_path)

        mock_send, *smtp_patches = self._smtp_patches()
        mock_imap = MagicMock()
        mock_imap.append.return_value = ("OK", [b""])
        mock_imap.select.return_value = ("OK", [b"1"])
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch("app.modules.mail.services.send._imap_connect", return_value=mock_imap),
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["gemma@example.com"],
                    "subject": "Re: NEW ROLE",
                    "body_plain": "Reply body",
                    "reply_to_message_id": str(row_id),
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        sent_bytes = mock_send.call_args[0][3]
        msg = _email.message_from_bytes(sent_bytes)
        assert msg["In-Reply-To"] == "<179248033325.8@example.com>"
        assert (msg["References"] or "").strip().endswith("<179248033325.8@example.com>")

    def test_send_with_unknown_reply_to_message_id_returns_400(self, app, mail_api):
        client, token, account_id, _cache_path = mail_api
        _set_account_secret(app, account_id)

        mock_send, *smtp_patches = self._smtp_patches()
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
        ):
            resp = client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["gemma@example.com"],
                    "subject": "Re: nothing",
                    "body_plain": "Reply body",
                    "reply_to_message_id": "99999",
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"]["code"] == "VALIDATION_ERROR"
        mock_send.assert_not_called()


class TestGetThreadPayloads:
    """U15.22/U15.25: imap_uid exposure (#5) and body options (#8)."""

    def test_thread_messages_expose_imap_uid_and_folder(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/threads/offer-thread-001",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        assert len(items) == 1
        assert items[0]["imap_uid"] == "100"
        assert items[0]["folder"] == "INBOX"

    def test_thread_include_bodies_false_omits_bodies(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/threads/offer-thread-001?include_bodies=false",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        assert "body_plain" not in items[0]
        assert "body_html" not in items[0]
        assert items[0]["snippet"]

    def test_thread_body_format_plain_strips_html(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/threads/offer-thread-001?body_format=plain",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        assert "body_html" not in items[0]
        assert items[0]["body_plain"]

    def test_thread_invalid_body_format_returns_400(self, app, mail_api):
        client, token, _account_id, _cache_path = mail_api
        resp = client.get(
            "/api/v1/mail/threads/x?body_format=rich",
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_list_messages_expose_imap_uid(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        resp = client.get(
            "/api/v1/mail/folders/INBOX/messages",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        assert all("imap_uid" in m for m in items)

    def test_get_message_exposes_imap_uid(self, app, mail_api):
        client, token, _account_id, cache_path = mail_api
        _seed_mail_cache(cache_path)
        list_resp = client.get(
            "/api/v1/mail/folders/INBOX/messages",
            headers=auth_header(token),
        )
        msg_id = json.loads(list_resp.data)["data"][0]["id"]
        resp = client.get(
            f"/api/v1/mail/messages/{msg_id}",
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["imap_uid"] == "100"
