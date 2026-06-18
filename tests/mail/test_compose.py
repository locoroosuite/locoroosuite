from unittest.mock import patch, MagicMock

import pytest

from app.shared.models.core import CustomerAccount, Domain
from app.modules.mail.controllers.helpers import _pending_sends, _pending_sends_lock


class TestCompose:
    def test_compose_page_renders(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = client.get("/app/mail/compose")
        assert resp.status_code == 200

    def test_send_mail_redirects(self, app, authed_client):
        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "test@test.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            assert "/mail/" in resp.headers["Location"]
        finally:
            with _pending_sends_lock:
                for token in list(_pending_sends):
                    if _pending_sends[token].get("user_id") == user_id:
                        _pending_sends.pop(token, None)

    def test_send_mail_includes_date_header(self, app, authed_client):
        from email import message_from_bytes
        from email.utils import parsedate_to_datetime

        client, user_id, account_id = authed_client
        captured_msg = None
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker") as mock_worker, \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "test@test.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                captured_msg = _pending_sends[tokens[0]]["msg"]
            parsed = message_from_bytes(captured_msg)
            date_header = parsed.get("Date")
            assert date_header is not None, "Sent message must have a Date header"
            parsedate_to_datetime(date_header)
        finally:
            with _pending_sends_lock:
                for token in list(_pending_sends):
                    if _pending_sends[token].get("user_id") == user_id:
                        _pending_sends.pop(token, None)

    def test_undo_send(self, app, authed_client):
        client, user_id, account_id = authed_client
        token = "test-undo-token-123"
        with _pending_sends_lock:
            _pending_sends[token] = {
                "user_id": user_id,
                "status": "countdown",
            }
        try:
            resp = client.post("/app/mail/undo-send", data={"token": token})
            assert resp.status_code == 302
        finally:
            with _pending_sends_lock:
                _pending_sends.pop(token, None)

    def test_save_draft(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append") as mock_append, \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            mock_append.return_value = ("OK", None)
            resp = client.post("/app/mail/draft", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Draft",
                "body_html": "<p>draft</p>",
            })
        assert resp.status_code == 302

    def test_save_draft_includes_date_header(self, app, authed_client):
        from email import message_from_bytes
        from email.utils import parsedate_to_datetime

        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        captured_bytes = None

        def capture_append(client_arg, folder, raw_bytes, **kwargs):
            nonlocal captured_bytes
            captured_bytes = raw_bytes
            return ("OK", None)

        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append", side_effect=capture_append), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            resp = client.post("/app/mail/draft", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Draft",
                "body_html": "<p>draft</p>",
            })
        assert resp.status_code == 302
        assert captured_bytes is not None
        parsed = message_from_bytes(captured_bytes)
        date_header = parsed.get("Date")
        assert date_header is not None, "Draft message must have a Date header"
        parsedate_to_datetime(date_header)


class TestSendRedirectAndSession:
    def test_send_stores_active_send_in_session(self, app, authed_client):
        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "test@test.com",
                    "subject": "My Test Subject",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            assert "/mail/" in resp.headers["Location"]
            with client.session_transaction() as sess:
                active = sess.get("active_send")
                assert active is not None
                assert "token" in active
                assert active["subject"] == "My Test Subject"
        finally:
            with _pending_sends_lock:
                for token in list(_pending_sends):
                    if _pending_sends[token].get("user_id") == user_id:
                        _pending_sends.pop(token, None)

    def test_send_status_page_redirects_to_mailbox(self, app, authed_client):
        client, user_id, account_id = authed_client
        token = "test-status-redirect"
        with _pending_sends_lock:
            _pending_sends[token] = {"user_id": user_id, "status": "countdown"}
        try:
            resp = client.get(f"/app/mail/send/{token}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Redirecting to inbox" in html
        finally:
            with _pending_sends_lock:
                _pending_sends.pop(token, None)

    def test_dismiss_send_clears_session(self, app, authed_client):
        client, user_id, account_id = authed_client
        with client.session_transaction() as sess:
            sess["active_send"] = {"token": "abc", "subject": "Test"}
        resp = client.post("/app/mail/send/dismiss")
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("active_send") is None

    def test_undo_send_clears_active_send_session(self, app, authed_client):
        client, user_id, account_id = authed_client
        token = "test-undo-session"
        with _pending_sends_lock:
            _pending_sends[token] = {"user_id": user_id, "status": "countdown"}
        with client.session_transaction() as sess:
            sess["active_send"] = {"token": token, "subject": "Test"}
        try:
            resp = client.post("/app/mail/undo-send", data={"token": token},
                               headers={"Accept": "application/json"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data.get("state") == "cancelled" or data.get("status") == "cancelled"
            with client.session_transaction() as sess:
                assert sess.get("active_send") is None
        finally:
            with _pending_sends_lock:
                _pending_sends.pop(token, None)

    def test_active_send_context_in_template(self, app, authed_client):
        client, user_id, account_id = authed_client
        with client.session_transaction() as sess:
            sess["active_send"] = {"token": "ctx-test", "subject": "Hello World"}
        try:
            resp = client.get("/app/mail/compose")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "send-widget" in html
            assert "Hello World" in html
            assert 'data-token="ctx-test"' in html
        finally:
            with client.session_transaction() as sess:
                sess.pop("active_send", None)


def _cleanup_user_sends(user_id):
    with _pending_sends_lock:
        for token in list(_pending_sends):
            if _pending_sends[token].get("user_id") == user_id:
                _pending_sends.pop(token, None)


class TestSendWithCc:
    def test_send_with_cc_includes_cc_header(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "cc": "cc@example.com",
                    "subject": "Test CC",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            parsed = message_from_bytes(payload["msg"])
            assert parsed.get("Cc") == "cc@example.com"
            assert parsed.get("To") == "to@example.com"
        finally:
            _cleanup_user_sends(user_id)

    def test_send_with_cc_stored_in_payload(self, app, authed_client):
        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "cc": "cc@example.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            assert payload["cc_addrs"] == "cc@example.com"
        finally:
            _cleanup_user_sends(user_id)

    def test_send_without_cc_has_no_cc_header(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            parsed = message_from_bytes(payload["msg"])
            assert parsed.get("Cc") is None
        finally:
            _cleanup_user_sends(user_id)

    def test_send_with_multiple_cc(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "cc": "cc1@example.com, cc2@example.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            parsed = message_from_bytes(payload["msg"])
            cc_header = parsed.get("Cc")
            assert "cc1@example.com" in cc_header
            assert "cc2@example.com" in cc_header
        finally:
            _cleanup_user_sends(user_id)


class TestSendWithBcc:
    def test_send_with_bcc_no_bcc_header_in_smtp_message(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "bcc": "bcc@example.com",
                    "subject": "Test BCC",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            parsed = message_from_bytes(payload["msg"])
            assert parsed.get("Bcc") is None, "BCC must not appear in SMTP message headers"
        finally:
            _cleanup_user_sends(user_id)

    def test_send_with_bcc_stored_in_payload(self, app, authed_client):
        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "bcc": "bcc@example.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            assert payload["bcc_addrs"] == "bcc@example.com"
        finally:
            _cleanup_user_sends(user_id)

    def test_send_with_bcc_preserved_in_sent_copy(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "bcc": "bcc@example.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            assert payload.get("sent_msg") is not None
            assert payload["sent_msg"] != payload["msg"]
            sent_parsed = message_from_bytes(payload["sent_msg"])
            assert sent_parsed.get("Bcc") == "bcc@example.com", "Sent copy must include BCC header"
            smtp_parsed = message_from_bytes(payload["msg"])
            assert smtp_parsed.get("Bcc") is None, "SMTP message must not include BCC header"
        finally:
            _cleanup_user_sends(user_id)

    def test_send_without_bcc_no_sent_msg_override(self, app, authed_client):
        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            assert payload["sent_msg"] == payload["msg"]
        finally:
            _cleanup_user_sends(user_id)

    def test_send_with_multiple_bcc(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "bcc": "bcc1@example.com, bcc2@example.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            sent_parsed = message_from_bytes(payload["sent_msg"])
            bcc_header = sent_parsed.get("Bcc")
            assert "bcc1@example.com" in bcc_header
            assert "bcc2@example.com" in bcc_header
        finally:
            _cleanup_user_sends(user_id)


class TestSendWithCcAndBcc:
    def test_send_with_to_cc_and_bcc(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "to@example.com",
                    "cc": "cc@example.com",
                    "bcc": "bcc@example.com",
                    "subject": "Test All",
                    "body_html": "<p>hi</p>",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            smtp_parsed = message_from_bytes(payload["msg"])
            assert smtp_parsed.get("To") == "to@example.com"
            assert smtp_parsed.get("Cc") == "cc@example.com"
            assert smtp_parsed.get("Bcc") is None
            sent_parsed = message_from_bytes(payload["sent_msg"])
            assert sent_parsed.get("Bcc") == "bcc@example.com"
        finally:
            _cleanup_user_sends(user_id)


class TestSendWorkerRecipients:
    def _make_account_domain_side_effect(self, account_id, domain_id):
        mock_account = MagicMock()
        mock_account.is_active = True
        mock_account.id = account_id
        mock_domain = MagicMock()
        mock_domain.is_active = True
        mock_domain.smtp_host = "smtp.example.com"
        mock_domain.smtp_port = 587
        mock_domain.smtp_tls_mode = "starttls"
        lookup = {}
        if account_id is not None:
            lookup[(CustomerAccount, account_id)] = mock_account
        if domain_id is not None:
            lookup[(Domain, domain_id)] = mock_domain

        def _get(model, pk):
            return lookup.get((model, pk))
        return _get, mock_account, mock_domain

    def test_send_worker_extracts_all_recipients(self, app, authed_client):
        from app.modules.mail.controllers.helpers import _send_worker

        client, user_id, account_id = authed_client
        captured_recipients = []

        def mock_smtp_send(server, from_addr, recipients, msg_bytes):
            captured_recipients.extend(recipients)

        token = "test-rcpt-token"
        with _pending_sends_lock:
            _pending_sends[token] = {
                "user_id": user_id,
                "account_id": account_id,
                "domain_id": 1,
                "auth_type": "password",
                "secret": "pass",
                "from_addr": "test@example.com",
                "to_addrs": "to@example.com",
                "cc_addrs": "cc@example.com",
                "bcc_addrs": "bcc@example.com",
                "subject": "Test",
                "body_html": "<p>hi</p>",
                "msg": b"",
                "sent_msg": b"",
                "status": "countdown",
                "send_after": 0,
                "created_at": 0,
                "updated_at": 0,
                "error": None,
                "warning": None,
            }

        side_effect, _, _ = self._make_account_domain_side_effect(account_id, 1)
        try:
            with app.app_context():
                with patch("app.modules.mail.controllers.helpers.smtp_connect") as mock_conn, \
                     patch("app.modules.mail.controllers.helpers.smtp_login"), \
                     patch("app.modules.mail.controllers.helpers.smtp_send", side_effect=mock_smtp_send), \
                     patch("app.modules.mail.controllers.helpers._imap_for_account", side_effect=Exception("skip")), \
                     patch("app.modules.mail.controllers.helpers.db.session.get", side_effect=side_effect):
                    mock_conn.return_value = MagicMock()
                    _send_worker(app, token, delay_seconds=0)

            assert "to@example.com" in captured_recipients
            assert "cc@example.com" in captured_recipients
            assert "bcc@example.com" in captured_recipients
        finally:
            _cleanup_user_sends(user_id)

    def test_send_worker_handles_display_names_with_commas(self, app, authed_client):
        from app.modules.mail.controllers.helpers import _send_worker

        client, user_id, account_id = authed_client
        captured_recipients = []

        def mock_smtp_send(server, from_addr, recipients, msg_bytes):
            captured_recipients.extend(recipients)

        token = "test-comma-token"
        with _pending_sends_lock:
            _pending_sends[token] = {
                "user_id": user_id,
                "account_id": account_id,
                "domain_id": 1,
                "auth_type": "password",
                "secret": "pass",
                "from_addr": "test@example.com",
                "to_addrs": '"Doe, John" <john@example.com>, jane@example.com',
                "cc_addrs": "",
                "bcc_addrs": "",
                "subject": "Test",
                "body_html": "<p>hi</p>",
                "msg": b"",
                "sent_msg": b"",
                "status": "countdown",
                "send_after": 0,
                "created_at": 0,
                "updated_at": 0,
                "error": None,
                "warning": None,
            }

        side_effect, _, _ = self._make_account_domain_side_effect(account_id, 1)
        try:
            with app.app_context():
                with patch("app.modules.mail.controllers.helpers.smtp_connect") as mock_conn, \
                     patch("app.modules.mail.controllers.helpers.smtp_login"), \
                     patch("app.modules.mail.controllers.helpers.smtp_send", side_effect=mock_smtp_send), \
                     patch("app.modules.mail.controllers.helpers._imap_for_account", side_effect=Exception("skip")), \
                     patch("app.modules.mail.controllers.helpers.db.session.get", side_effect=side_effect):
                    mock_conn.return_value = MagicMock()
                    _send_worker(app, token, delay_seconds=0)

            assert "john@example.com" in captured_recipients
            assert "jane@example.com" in captured_recipients
            assert len(captured_recipients) == 2
        finally:
            _cleanup_user_sends(user_id)

    def test_send_worker_deduplicates_recipients(self, app, authed_client):
        from app.modules.mail.controllers.helpers import _send_worker

        client, user_id, account_id = authed_client
        captured_recipients = []

        def mock_smtp_send(server, from_addr, recipients, msg_bytes):
            captured_recipients.extend(recipients)

        token = "test-dedup-token"
        with _pending_sends_lock:
            _pending_sends[token] = {
                "user_id": user_id,
                "account_id": account_id,
                "domain_id": 1,
                "auth_type": "password",
                "secret": "pass",
                "from_addr": "test@example.com",
                "to_addrs": "dup@example.com",
                "cc_addrs": "dup@example.com",
                "bcc_addrs": "dup@example.com",
                "subject": "Test",
                "body_html": "<p>hi</p>",
                "msg": b"",
                "sent_msg": b"",
                "status": "countdown",
                "send_after": 0,
                "created_at": 0,
                "updated_at": 0,
                "error": None,
                "warning": None,
            }

        side_effect, _, _ = self._make_account_domain_side_effect(account_id, 1)
        try:
            with app.app_context():
                with patch("app.modules.mail.controllers.helpers.smtp_connect") as mock_conn, \
                     patch("app.modules.mail.controllers.helpers.smtp_login"), \
                     patch("app.modules.mail.controllers.helpers.smtp_send", side_effect=mock_smtp_send), \
                     patch("app.modules.mail.controllers.helpers._imap_for_account", side_effect=Exception("skip")), \
                     patch("app.modules.mail.controllers.helpers.db.session.get", side_effect=side_effect):
                    mock_conn.return_value = MagicMock()
                    _send_worker(app, token, delay_seconds=0)

            assert captured_recipients.count("dup@example.com") == 1
        finally:
            _cleanup_user_sends(user_id)


class TestDraftCcBcc:
    def test_draft_with_cc_includes_cc_header(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        captured_bytes = None

        def capture_append(client_arg, folder, raw_bytes, **kwargs):
            nonlocal captured_bytes
            captured_bytes = raw_bytes
            return ("OK", None)

        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append", side_effect=capture_append), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (MagicMock(), MagicMock())
            resp = client.post("/app/mail/draft", data={
                "account_id": account_id,
                "to": "to@example.com",
                "cc": "cc@example.com",
                "bcc": "bcc@example.com",
                "subject": "Draft",
                "body_html": "<p>draft</p>",
            })
        assert resp.status_code == 302
        parsed = message_from_bytes(captured_bytes)
        assert parsed.get("Cc") == "cc@example.com"
        assert parsed.get("Bcc") == "bcc@example.com"

    def test_draft_without_cc_bcc_has_no_empty_headers(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        captured_bytes = None

        def capture_append(client_arg, folder, raw_bytes, **kwargs):
            nonlocal captured_bytes
            captured_bytes = raw_bytes
            return ("OK", None)

        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append", side_effect=capture_append), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (MagicMock(), MagicMock())
            resp = client.post("/app/mail/draft", data={
                "account_id": account_id,
                "to": "to@example.com",
                "subject": "Draft",
                "body_html": "<p>draft</p>",
            })
        assert resp.status_code == 302
        parsed = message_from_bytes(captured_bytes)
        assert parsed.get("Cc") is None
        assert parsed.get("Bcc") is None


class TestComposePrefill:
    def test_compose_with_cc_prefill_shows_cc_row(self, app, authed_client):
        client, user_id, account_id = authed_client
        token = "test-prefill-cc"
        with _pending_sends_lock:
            _pending_sends[token] = {
                "user_id": user_id,
                "account_id": account_id,
                "to_addrs": "to@example.com",
                "cc_addrs": "cc@example.com",
                "bcc_addrs": "",
                "subject": "Prefill",
                "body_html": "<p>hi</p>",
                "attachments_count": 0,
            }
        try:
            resp = client.get(f"/app/mail/compose?prefill_token={token}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'data-cc-row' in html
            assert "cc@example.com" in html
        finally:
            _cleanup_user_sends(user_id)

    def test_compose_with_bcc_prefill_shows_bcc_row(self, app, authed_client):
        client, user_id, account_id = authed_client
        token = "test-prefill-bcc"
        with _pending_sends_lock:
            _pending_sends[token] = {
                "user_id": user_id,
                "account_id": account_id,
                "to_addrs": "to@example.com",
                "cc_addrs": "",
                "bcc_addrs": "bcc@example.com",
                "subject": "Prefill",
                "body_html": "<p>hi</p>",
                "attachments_count": 0,
            }
        try:
            resp = client.get(f"/app/mail/compose?prefill_token={token}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'data-bcc-row' in html
            assert "bcc@example.com" in html
        finally:
            _cleanup_user_sends(user_id)


class TestAutoSaveDraft:
    def test_auto_save_empty_body_returns_null_uid(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = client.post("/app/mail/draft/auto-save", data={
            "account_id": account_id,
            "to": "test@test.com",
            "subject": "Test",
            "body_html": "",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["draft_uid"] is None

    def test_auto_save_empty_html_tags_returns_null_uid(self, app, authed_client):
        client, user_id, account_id = authed_client
        resp = client.post("/app/mail/draft/auto-save", data={
            "account_id": account_id,
            "to": "test@test.com",
            "subject": "Test",
            "body_html": "<p><br></p>",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["draft_uid"] is None

    def test_auto_save_with_body_creates_draft(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append") as mock_append, \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            mock_append.return_value = ("OK", [b"[APPENDUID 123 42] APPEND completed."])
            resp = client.post("/app/mail/draft/auto-save", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Test",
                "body_html": "<p>Hello</p>",
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["draft_uid"] == "42"

    def test_auto_save_replaces_existing_draft(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append") as mock_append, \
             patch("app.modules.mail.controllers.compose.delete_message_by_uid") as mock_delete, \
             patch("app.modules.mail.controllers.compose.select_folder"), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            mock_append.return_value = ("OK", [b"[APPENDUID 123 99] APPEND completed."])
            resp = client.post("/app/mail/draft/auto-save", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Test",
                "body_html": "<p>Updated</p>",
                "draft_uid": "42",
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["draft_uid"] == "99"
        mock_delete.assert_called_once_with(mock_imap_client, "42")

    def test_auto_save_no_old_uid_skips_delete(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append") as mock_append, \
             patch("app.modules.mail.controllers.compose.delete_message_by_uid") as mock_delete, \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            mock_append.return_value = ("OK", [b"[APPENDUID 123 10] APPEND completed."])
            resp = client.post("/app/mail/draft/auto-save", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Test",
                "body_html": "<p>Hello</p>",
            })
        assert resp.status_code == 200
        mock_delete.assert_not_called()

    def test_auto_save_returns_error_on_imap_failure(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append", side_effect=Exception("IMAP down")), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            resp = client.post("/app/mail/draft/auto-save", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Test",
                "body_html": "<p>Hello</p>",
            })
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False

    def test_auto_save_invalid_account_returns_404(self, app, authed_client):
        client, user_id, _account_id = authed_client
        resp = client.post("/app/mail/draft/auto-save", data={
            "account_id": 99999,
            "to": "test@test.com",
            "subject": "Test",
            "body_html": "<p>Hello</p>",
        })
        assert resp.status_code == 404


class TestSendDeletesDraft:
    def test_send_stores_draft_uid_in_payload(self, app, authed_client):
        client, user_id, account_id = authed_client
        try:
            with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
                 patch("app.modules.mail.controllers.compose._start_send_worker"), \
                 patch("app.modules.mail.controllers.compose._cleanup_pending_sends"):
                resp = client.post("/app/mail/send", data={
                    "account_id": account_id,
                    "to": "test@test.com",
                    "subject": "Test",
                    "body_html": "<p>hi</p>",
                    "draft_uid": "42",
                })
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                payload = _pending_sends[tokens[0]]
            assert payload["draft_uid"] == "42"
        finally:
            _cleanup_user_sends(user_id)

    def test_send_worker_deletes_draft_and_cleans_cache(self, app, authed_client):
        from app.modules.mail.controllers.helpers import _send_worker

        client, user_id, account_id = authed_client
        token = "test-draft-delete-token"

        def _make_account_domain_side_effect(account_id, domain_id):
            mock_account = MagicMock()
            mock_account.is_active = True
            mock_account.id = account_id
            mock_domain = MagicMock()
            mock_domain.is_active = True
            mock_domain.smtp_host = "smtp.example.com"
            mock_domain.smtp_port = 587
            mock_domain.smtp_tls_mode = "starttls"
            lookup = {}
            if account_id is not None:
                lookup[(CustomerAccount, account_id)] = mock_account
            if domain_id is not None:
                lookup[(Domain, domain_id)] = mock_domain

            def _get(model, pk):
                return lookup.get((model, pk))
            return _get, mock_account, mock_domain

        with _pending_sends_lock:
            _pending_sends[token] = {
                "user_id": user_id,
                "account_id": account_id,
                "domain_id": 1,
                "auth_type": "password",
                "secret": "pass",
                "from_addr": "test@example.com",
                "to_addrs": "to@example.com",
                "cc_addrs": "",
                "bcc_addrs": "",
                "subject": "Test",
                "body_html": "<p>hi</p>",
                "msg": b"",
                "sent_msg": b"",
                "draft_uid": "42",
                "status": "countdown",
                "send_after": 0,
                "created_at": 0,
                "updated_at": 0,
                "error": None,
                "warning": None,
            }

        side_effect, _, _ = _make_account_domain_side_effect(account_id, 1)
        mock_imap = MagicMock()
        mock_sync = MagicMock()
        try:
            with app.app_context():
                app.sync_manager = mock_sync
                with patch("app.modules.mail.controllers.helpers.smtp_connect") as mock_conn, \
                     patch("app.modules.mail.controllers.helpers.smtp_login"), \
                     patch("app.modules.mail.controllers.helpers.smtp_send"), \
                     patch("app.modules.mail.controllers.helpers._imap_for_account", return_value=(mock_imap, MagicMock())), \
                     patch("app.modules.mail.controllers.helpers.select_folder"), \
                     patch("app.modules.mail.controllers.helpers.delete_message_by_uid") as mock_delete, \
                     patch("app.modules.mail.controllers.helpers.open_cache", return_value=MagicMock()) as mock_open_cache, \
                     patch("app.modules.mail.controllers.helpers.delete_messages_by_uids") as mock_cache_delete, \
                     patch("app.modules.mail.controllers.helpers.get_user_key", return_value="key"), \
                     patch("app.modules.mail.controllers.helpers.db.session.get", side_effect=side_effect):
                    mock_conn.return_value = MagicMock()
                    _send_worker(app, token, delay_seconds=0)

            mock_delete.assert_called_once_with(mock_imap, "42")
            mock_cache_delete.assert_called_once()
            assert mock_cache_delete.call_args[0][1] == "Drafts"
            assert mock_cache_delete.call_args[0][2] == ["42"]
            sync_folders = [c.kwargs.get("folder") or c.args[1] if len(c.args) > 1 else c.kwargs.get("folder") for c in mock_sync.enqueue_sync.call_args_list]
            assert "Drafts" in sync_folders
        finally:
            _cleanup_user_sends(user_id)


class TestDraftFlag:
    def test_auto_save_sets_draft_flag(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        captured_flags = None

        def capture_append(client_arg, folder, raw_bytes, flags=None, **kwargs):
            nonlocal captured_flags
            captured_flags = flags
            return ("OK", [b"[APPENDUID 123 42] APPEND completed."])

        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append", side_effect=capture_append), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            resp = client.post("/app/mail/draft/auto-save", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Test",
                "body_html": "<p>Hello</p>",
            })
        assert resp.status_code == 200
        assert captured_flags == ["\\Draft"]

    def test_manual_save_sets_draft_flag(self, app, authed_client):
        from email import message_from_bytes

        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        captured_flags = None

        def capture_append(client_arg, folder, raw_bytes, flags=None, **kwargs):
            nonlocal captured_flags
            captured_flags = flags
            return ("OK", None)

        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append", side_effect=capture_append), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            resp = client.post("/app/mail/draft", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Draft",
                "body_html": "<p>draft</p>",
            })
        assert resp.status_code == 302
        assert captured_flags == ["\\Draft"]

    def test_manual_save_replaces_old_draft(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.ensure_folder_and_append") as mock_append, \
             patch("app.modules.mail.controllers.compose.delete_message_by_uid") as mock_delete, \
             patch("app.modules.mail.controllers.compose.select_folder"), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            mock_append.return_value = ("OK", None)
            resp = client.post("/app/mail/draft", data={
                "account_id": account_id,
                "to": "test@test.com",
                "subject": "Draft",
                "body_html": "<p>draft</p>",
                "draft_uid": "42",
            })
        assert resp.status_code == 302
        mock_delete.assert_called_once_with(mock_imap_client, "42")


class TestComposeDraftResume:
    def test_compose_with_draft_uid_loads_prefill(self, app, authed_client):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        client, user_id, account_id = authed_client

        msg = MIMEMultipart("mixed")
        msg["To"] = "recipient@test.com"
        msg["Cc"] = "cc@test.com"
        msg["Subject"] = "Re: Hello"
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText("plain body", "plain"))
        alt.attach(MIMEText("<p>html body</p>", "html"))
        msg.attach(alt)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"uid": "42", "folder": "Drafts", "flags": '["\\\\Draft"]'}

        with patch("app.modules.mail.controllers.compose.open_cache", return_value=mock_conn), \
             patch("app.modules.mail.controllers.compose.get_user_key"), \
             patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.select_folder"), \
             patch("app.modules.mail.controllers.compose.fetch_message", return_value=msg), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (MagicMock(), MagicMock())
            resp = client.get(f"/app/mail/compose?account_id={account_id}&draft_uid=42")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "recipient@test.com" in html
        assert "Re: Hello" in html

    def test_compose_draft_uid_not_found_renders_blank(self, app, authed_client):
        client, user_id, account_id = authed_client

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None

        with patch("app.modules.mail.controllers.compose.open_cache", return_value=mock_conn), \
             patch("app.modules.mail.controllers.compose.get_user_key"):
            resp = client.get(f"/app/mail/compose?account_id={account_id}&draft_uid=9999")
        assert resp.status_code == 200


class TestDiscardDraft:
    def test_discard_draft_deletes_from_imap_and_cache(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        mock_conn = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.select_folder"), \
             patch("app.modules.mail.controllers.compose.delete_message_by_uid") as mock_delete, \
             patch("app.modules.mail.controllers.compose.safe_logout"), \
             patch("app.modules.mail.controllers.compose.open_cache", return_value=mock_conn), \
             patch("app.modules.mail.controllers.compose.delete_messages_by_uids") as mock_cache_delete:
            mock_imap.return_value = (mock_imap_client, MagicMock())
            resp = client.post(f"/app/mail/draft/{account_id}/42/discard")
        assert resp.status_code == 302
        mock_delete.assert_called_once_with(mock_imap_client, "42")
        mock_cache_delete.assert_called_once_with(mock_conn, "Drafts", ["42"])

    def test_discard_draft_xhr_returns_json(self, app, authed_client):
        client, user_id, account_id = authed_client
        mock_imap_client = MagicMock()
        with patch("app.modules.mail.controllers.compose.decrypt_with_key"), \
             patch("app.modules.mail.controllers.compose._imap_for_account") as mock_imap, \
             patch("app.modules.mail.controllers.compose.select_folder"), \
             patch("app.modules.mail.controllers.compose.delete_message_by_uid"), \
             patch("app.modules.mail.controllers.compose.safe_logout"):
            mock_imap.return_value = (mock_imap_client, MagicMock())
            resp = client.post(
                f"/app/mail/draft/{account_id}/42/discard",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_discard_draft_invalid_account_404(self, app, authed_client):
        client, user_id, _account_id = authed_client
        resp = client.post("/app/mail/draft/99999/42/discard")
        assert resp.status_code == 404
