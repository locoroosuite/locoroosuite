import re
import time
import uuid

import pytest
import requests

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import (
    MAIL_API_KEY,
    MAIL_API_URL,
    E2E_DEFAULT_PASSWORD,
    admin_session,
    get_account_id,
    imap_folder_has_message,
    login_session,
    mailapi_create_user,
    mailapi_delete_user,
    mailapi_user_exists,
    wait_for,
    _extract_domain_id,
)


@skip_if_no_services
class TestFolderList:
    def test_folder_list_loads(self, app_url, user_session, user_account_id):
        r = user_session.get(f"{app_url}/app/mail/folder/{user_account_id}/INBOX", allow_redirects=True)
        assert r.status_code == 200
        for name in ("INBOX", "Sent", "Drafts", "Trash"):
            assert name in r.text


@skip_if_no_services
class TestMessageList:
    def test_message_list_loads(self, app_url, user_session, user_account_id):
        r = user_session.get(
            f"{app_url}/app/mail/folder/{user_account_id}/INBOX", allow_redirects=True
        )
        assert r.status_code == 200


@skip_if_no_services
class TestSendEmail:
    def test_send_email_delivers_to_imap(self, app_url, user_session, user_account_id):
        subject = f"E2E test {uuid.uuid4().hex[:8]}"
        r = user_session.post(
            f"{app_url}/app/mail/send",
            data={
                "account_id": user_account_id,
                "to": "e2e-test2@test.localhost",
                "subject": subject,
                "body_html": f"<p>Test body {subject}</p>",
            },
            allow_redirects=True,
        )
        assert r.status_code == 200
        assert imap_folder_has_message(
            "e2e-test2@test.localhost",
            E2E_DEFAULT_PASSWORD,
            "INBOX",
            subject_contains=subject,
            timeout=30,
        )


@skip_if_no_services
class TestMessageDetail:
    def test_message_detail_loads(self, app_url, user_session, user_account_id):
        subject = f"E2E detail {uuid.uuid4().hex[:8]}"
        user_session.post(
            f"{app_url}/app/mail/send",
            data={
                "account_id": user_account_id,
                "to": "e2e-test@test.localhost",
                "subject": subject,
                "body_html": f"<p>{subject}</p>",
            },
            allow_redirects=True,
        )
        assert imap_folder_has_message(
            "e2e-test@test.localhost",
            E2E_DEFAULT_PASSWORD,
            "INBOX",
            subject_contains=subject,
            timeout=30,
        )
        msg_id = wait_for(
            lambda: _find_message_id(user_session, app_url, user_account_id, "INBOX", subject),
            timeout=20,
        )
        r = user_session.get(
            f"{app_url}/app/mail/message/{user_account_id}/{msg_id}", allow_redirects=True
        )
        assert r.status_code == 200


@skip_if_no_services
class TestMoveMessage:
    def test_move_message_to_trash(self, app_url, user_session, user_account_id):
        subject = f"E2E move {uuid.uuid4().hex[:8]}"
        user_session.post(
            f"{app_url}/app/mail/send",
            data={
                "account_id": user_account_id,
                "to": "e2e-test@test.localhost",
                "subject": subject,
                "body_html": f"<p>{subject}</p>",
            },
            allow_redirects=True,
        )
        assert imap_folder_has_message(
            "e2e-test@test.localhost",
            E2E_DEFAULT_PASSWORD,
            "INBOX",
            subject_contains=subject,
            timeout=30,
        )
        msg_id = wait_for(
            lambda: _find_message_id(user_session, app_url, user_account_id, "INBOX", subject),
            timeout=20,
        )
        assert msg_id is not None, f"Message with subject '{subject}' not found in INBOX"
        r = user_session.post(
            f"{app_url}/app/mail/message/{user_account_id}/{msg_id}/move",
            data={"destination": "Trash"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert r.status_code == 200
        try:
            assert imap_folder_has_message(
                "e2e-test@test.localhost",
                E2E_DEFAULT_PASSWORD,
                "Trash",
                subject_contains=subject,
                timeout=60,
            )
        except (TimeoutError, AssertionError):
            msg_id2 = _find_message_id(user_session, app_url, user_account_id, "INBOX", subject)
            if msg_id2:
                r2 = user_session.post(
                    f"{app_url}/app/mail/message/{user_account_id}/{msg_id2}/move",
                    data={"destination": "Trash"},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                assert r2.status_code == 200
            assert imap_folder_has_message(
                "e2e-test@test.localhost",
                E2E_DEFAULT_PASSWORD,
                "Trash",
                subject_contains=subject,
                timeout=60,
            )


def _find_message_id(session, app_url, account_id, folder, subject):
    r = session.get(f"{app_url}/app/mail/folder/{account_id}/{folder}")
    if r.status_code != 200:
        return None
    ids = re.findall(r'data-message-id="(\d+)"', r.text)
    for mid in ids:
        detail = session.get(
            f"{app_url}/app/mail/message/{account_id}/{mid}",
            allow_redirects=True,
        )
        if detail.status_code == 200 and subject in detail.text:
            return mid
    return None


def _set_sending_limit(email: str, max_per_day: int):
    r = requests.post(
        f"{MAIL_API_URL}/api/users/{email}/sending-limit",
        headers={"Authorization": f"Bearer {MAIL_API_KEY}"},
        json={"max_per_day": max_per_day},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def _get_sending_limit(email: str) -> dict:
    r = requests.get(
        f"{MAIL_API_URL}/api/users/{email}/sending-limit",
        headers={"Authorization": f"Bearer {MAIL_API_KEY}"},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def _poll_send_status(session, app_url, send_token, timeout=30):
    def _check():
        try:
            r = session.get(
                f"{app_url}/app/mail/send/status/{send_token}",
                headers={"Accept": "application/json"},
            )
        except Exception:
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None
        if data.get("state") in ("sent", "failed", "cancelled"):
            return data
        return None

    return wait_for(_check, timeout=timeout)


def _create_app_user(app_url, admin_sess, email, password=E2E_DEFAULT_PASSWORD):
    r = admin_sess.get(f"{app_url}/admin/customers")
    assert r.status_code == 200
    domain_id = _extract_domain_id(r.text, email.split("@")[1])
    assert domain_id, f"Domain not found for {email}"
    username = email.split("@")[0]
    r = admin_sess.post(
        f"{app_url}/admin/customers/new",
        data={
            "username": username,
            "domain_id": domain_id,
            "password": password,
            "create_mode": "password",
        },
        allow_redirects=True,
    )
    assert r.status_code == 200, f"Failed to create app user {email}: {r.status_code}"
    assert email in r.text, f"User {email} not found in customer list after creation — form may have returned errors"


@skip_if_no_services
class TestSendingLimit:
    def test_sending_limit_enforced(self, app_url):
        sender = f"e2e-limit-{uuid.uuid4().hex[:8]}@test.localhost"
        recipient = f"e2e-limit-rcpt-{uuid.uuid4().hex[:8]}@test.localhost"

        admin = admin_session()
        _create_app_user(app_url, admin, sender)
        _create_app_user(app_url, admin, recipient)
        _set_sending_limit(sender, max_per_day=2)

        try:
            wait_for(lambda: mailapi_user_exists(sender), timeout=15)
            wait_for(lambda: mailapi_user_exists(recipient), timeout=15)
            import requests as _requests
            _test_sess = _requests.Session()
            _login_r = _test_sess.post(f"{app_url}/app/login", data={"email": sender, "password": E2E_DEFAULT_PASSWORD}, allow_redirects=True)
            if "login" in _login_r.url and not _login_r.url.endswith("/mail/"):
                _error_msgs = re.findall(r'alert[^>]*>([^<]+)<', _login_r.text)
                _has_dovecot = mailapi_user_exists(sender)
                raise AssertionError(
                    f"Login failed for {sender}: url={_login_r.url} errors={_error_msgs} dovecot_exists={_has_dovecot}"
                )
            sess = _test_sess
            account_id = get_account_id(app_url, sess)

            subject1 = f"Limit test 1 {uuid.uuid4().hex[:8]}"
            r = sess.post(
                f"{app_url}/app/mail/send",
                data={
                    "account_id": account_id,
                    "to": recipient,
                    "subject": subject1,
                    "body_html": f"<p>{subject1}</p>",
                },
                allow_redirects=True,
            )
            assert r.status_code == 200
            token1_match = re.search(r'data-token="([^"]+)"', r.text)
            assert token1_match, f"Send widget token not found in response"
            token1 = token1_match.group(1)
            result = _poll_send_status(sess, app_url, token1)
            assert result["state"] == "sent", f"First send should succeed: {result}"

            limit = _get_sending_limit(sender)
            assert limit["sent_today"] == 1, f"sent_today should be 1: {limit}"

            subject2 = f"Limit test 2 {uuid.uuid4().hex[:8]}"
            r = sess.post(
                f"{app_url}/app/mail/send",
                data={
                    "account_id": account_id,
                    "to": recipient,
                    "subject": subject2,
                    "body_html": f"<p>{subject2}</p>",
                },
                allow_redirects=True,
            )
            assert r.status_code == 200
            token2_match = re.search(r'data-token="([^"]+)"', r.text)
            assert token2_match, f"Send widget token not found in response"
            token2 = token2_match.group(1)
            result = _poll_send_status(sess, app_url, token2)
            assert result["state"] == "sent", f"Second send should succeed: {result}"

            limit = _get_sending_limit(sender)
            assert limit["sent_today"] == 2, f"sent_today should be 2: {limit}"

            subject3 = f"Limit test 3 {uuid.uuid4().hex[:8]}"
            r = sess.post(
                f"{app_url}/app/mail/send",
                data={
                    "account_id": account_id,
                    "to": recipient,
                    "subject": subject3,
                    "body_html": f"<p>{subject3}</p>",
                },
                allow_redirects=True,
            )
            assert r.status_code == 200
            token3_match = re.search(r'data-token="([^"]+)"', r.text)
            assert token3_match, f"Send widget token not found in response"
            token3 = token3_match.group(1)
            result = _poll_send_status(sess, app_url, token3)
            assert result["state"] == "failed", f"Third send should fail: {result}"
            assert "Daily sending limit reached" in result["error"], (
                f"Error should mention sending limit: {result['error']}"
            )
            assert "contact support" in result["error"].lower(), (
                f"Error should suggest contacting support: {result['error']}"
            )

            limit = _get_sending_limit(sender)
            assert limit["sent_today"] == 2, f"sent_today should still be 2: {limit}"
        finally:
            try:
                _get_sending_limit(sender)
                requests.delete(
                    f"{MAIL_API_URL}/api/users/{sender}/sending-limit",
                    headers={"Authorization": f"Bearer {MAIL_API_KEY}"},
                    timeout=5,
                )
            except Exception:
                pass
            mailapi_delete_user(sender)
            mailapi_delete_user(recipient)
