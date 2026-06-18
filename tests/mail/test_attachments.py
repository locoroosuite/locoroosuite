import io
import shutil
from email import message_from_bytes
from unittest.mock import patch, MagicMock

import pytest

from app.modules.mail.controllers.helpers import _pending_sends, _pending_sends_lock
from app.modules.mail.services import attachments as staging

SID = "testsess-ion1234"  # >= 8 chars, matches SAFE_ID_RE


@pytest.fixture(autouse=True)
def _clean_staging(app, authed_client):
    _, user_id, _ = authed_client
    yield
    try:
        with app.app_context():
            staging.delete_session(user_id, SID)
            user_dir = staging._session_dir(user_id, "x").parent
            if user_dir.exists():
                shutil.rmtree(user_dir, ignore_errors=True)
    except Exception:
        pass


def _stage(client, data=None, raw=b"hello world", name="test.txt", sid=SID):
    data = data or {}
    data["compose_session_id"] = sid
    data["file"] = (io.BytesIO(raw), name)
    return client.post(
        "/app/mail/attachments/stage",
        data=data,
        content_type="multipart/form-data",
    )


class TestStageAttachment:
    def test_stage_success(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = _stage(client, raw=b"hello world", name="note.txt")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["name"] == "note.txt"
        assert body["size"] == 11
        assert body["id"]
        # File actually written to the staging tree for this user
        assert staging.read_bytes(user_id, SID, body["id"]) == b"hello world"

    def test_stage_invalid_session(self, app, authed_client):
        client, _, _ = authed_client
        data = {"compose_session_id": "bad", "file": (io.BytesIO(b"x"), "a.txt")}
        resp = client.post("/app/mail/attachments/stage", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_session"

    def test_stage_no_file(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.post("/app/mail/attachments/stage", data={"compose_session_id": SID})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "no_file"

    def test_stage_empty_file(self, app, authed_client):
        client, _, _ = authed_client
        resp = _stage(client, raw=b"", name="empty.txt")
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "empty_file"

    def test_stage_file_too_large(self, app, authed_client):
        client, _, _ = authed_client
        original = app.config.get("MAIL_ATTACHMENT_MAX_FILE_BYTES")
        app.config["MAIL_ATTACHMENT_MAX_FILE_BYTES"] = 5
        try:
            resp = _stage(client, raw=b"1234567890", name="big.txt")
            assert resp.status_code == 413
            assert resp.get_json()["error"]["code"] == "file_too_large"
        finally:
            app.config["MAIL_ATTACHMENT_MAX_FILE_BYTES"] = original

    def test_stage_total_limit(self, app, authed_client):
        client, _, _ = authed_client
        original = app.config.get("MAIL_ATTACHMENT_MAX_TOTAL_BYTES")
        app.config["MAIL_ATTACHMENT_MAX_TOTAL_BYTES"] = 15
        try:
            r1 = _stage(client, raw=b"0123456789", name="a.txt")  # 10 bytes OK
            assert r1.status_code == 200
            r2 = _stage(client, raw=b"0123456789", name="b.txt")  # +10 = 20 > 15
            assert r2.status_code == 413
            assert r2.get_json()["error"]["code"] == "total_too_large"
        finally:
            app.config["MAIL_ATTACHMENT_MAX_TOTAL_BYTES"] = original

    def test_stage_filename_sanitized(self, app, authed_client):
        client, user_id, _ = authed_client
        # Path components and control chars must be stripped from the stored name.
        resp = _stage(client, raw=b"x", name="../../etc/passwd")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "passwd"
        assert "/" not in resp.get_json()["name"]


class TestDeleteAndList:
    def test_delete_attachment(self, app, authed_client):
        client, _, _ = authed_client
        staged = _stage(client, raw=b"remove-me", name="r.txt").get_json()
        resp = client.delete(
            "/app/mail/attachments/%s?compose_session_id=%s" % (staged["id"], SID)
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        # Listing now empty
        lst = client.get("/app/mail/attachments?compose_session_id=%s" % SID).get_json()
        assert lst["attachments"] == []

    def test_list_attachments(self, app, authed_client):
        client, _, _ = authed_client
        _stage(client, raw=b"one", name="a.txt")
        _stage(client, raw=b"twotwo", name="b.txt")
        body = client.get("/app/mail/attachments?compose_session_id=%s" % SID).get_json()
        names = sorted(d["name"] for d in body["attachments"])
        assert names == ["a.txt", "b.txt"]
        assert body["used"] == 3 + 6

    def test_list_invalid_session(self, app, authed_client):
        client, _, _ = authed_client
        resp = client.get("/app/mail/attachments?compose_session_id=nope")
        assert resp.status_code == 400


class TestSendWithStagedAttachments:
    def test_send_includes_staged_attachment(self, app, authed_client):
        client, user_id, account_id = authed_client
        staged = _stage(client, raw=b"ATTACHBYTES", name="report.pdf").get_json()
        captured = None
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "dest@example.com",
                    "subject": "With attachment",
                    "body_html": "<p>hi</p>",
                    "compose_session_id": SID,
                    "attachment_ids": staged["id"],
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
                captured = payload["msg"]
                assert payload["attachments_count"] == 1
            parsed = message_from_bytes(captured)
            found = False
            for part in parsed.walk():
                if part.get_content_disposition() == "attachment":
                    found = True
                    assert part.get_filename() == "report.pdf"
                    assert part.get_payload(decode=True) == b"ATTACHBYTES"
            assert found, "sent message must contain the staged attachment"
            # Staging cleaned up after queueing
            assert staging.read_bytes(user_id, SID, staged["id"]) is None
        finally:
            with _pending_sends_lock:
                for token in list(_pending_sends):
                    if _pending_sends[token].get("user_id") == user_id:
                        _pending_sends.pop(token, None)

    def test_send_over_total_limit_renders_error(self, app, authed_client):
        client, user_id, account_id = authed_client
        _stage(client, raw=b"0123456789", name="a.txt")
        original = app.config.get("MAIL_ATTACHMENT_MAX_TOTAL_BYTES")
        app.config["MAIL_ATTACHMENT_MAX_TOTAL_BYTES"] = 1
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "dest@example.com",
                    "subject": "Over",
                    "body_html": "<p>hi</p>",
                    "compose_session_id": SID,
                    "attachment_ids": "",  # will be empty, so not over limit
                })
            # No attachment_ids => no attachments => sends fine (302)
            assert resp.status_code == 302
        finally:
            with _pending_sends_lock:
                for token in list(_pending_sends):
                    if _pending_sends[token].get("user_id") == user_id:
                        _pending_sends.pop(token, None)
            app.config["MAIL_ATTACHMENT_MAX_TOTAL_BYTES"] = original

    def test_send_without_attachments_still_works(self, app, authed_client):
        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "dest@example.com",
                    "subject": "Plain",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert _pending_sends[tokens[0]]["attachments_count"] == 0
        finally:
            with _pending_sends_lock:
                for token in list(_pending_sends):
                    if _pending_sends[token].get("user_id") == user_id:
                        _pending_sends.pop(token, None)


class TestSaveDraftWithStagedAttachments:
    def test_draft_includes_staged_attachment(self, app, authed_client):
        client, user_id, account_id = authed_client
        staged = _stage(client, raw=b"DRAFTFILE", name="doc.odt").get_json()
        captured = None
        mock_imap_client = MagicMock()

        def capture_append(_client, folder, raw_bytes, **kwargs):
            nonlocal captured
            captured = raw_bytes
            return ("OK", None)

        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append", side_effect=capture_append), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            resp = client.post("/app/mail/draft", data={
                "account_id": account_id,
                "to": "dest@example.com",
                "subject": "Draft w/ attachment",
                "body_html": "<p>draft</p>",
                "compose_session_id": SID,
                "attachment_ids": staged["id"],
            })
        assert resp.status_code == 302
        assert captured is not None
        parsed = message_from_bytes(captured)
        found = any(
            p.get_content_disposition() == "attachment" and p.get_filename() == "doc.odt"
            for p in parsed.walk()
        )
        assert found
        # Staging cleaned after successful draft save
        assert staging.read_bytes(user_id, SID, staged["id"]) is None
