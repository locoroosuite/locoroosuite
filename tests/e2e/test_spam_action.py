r"""E2E: the web spam actions against the real dev Dovecot (HLD U5.13/U5.13b).

Why these exist: every unit/API test mocks the IMAP client, so the product
shipped with `STORE +FLAGS (\Junk)` — a flag that is not an RFC 3501 system
flag and that Dovecot rejects with `BAD Invalid system flag \JUNK`. Every
"Report spam" on a Dovecot-hosted account failed and auto-disabled the
per-account setting (production incident, mrem@tangomalena.com). These tests
drive the real route and verify the effect at the IMAP service level:
message moved to Junk, `Junk` keyword set; "Not spam" moves back and clears
the keyword.
"""

import imaplib
import re
import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import (
    E2E_DEFAULT_PASSWORD,
    imap_connect,
    imap_folder_has_message,
    wait_for,
)

USER = "e2e-test@test.localhost"


def _imap_flags_with_subject(user, password, folder, subject_contains):
    """Return the FLAGS string of the message whose Subject contains the
    marker, or None when it is not in the folder."""
    conn = imap_connect(user, password)
    try:
        status, _ = conn.select(folder)
        if status != "OK":
            return None
        status, data = conn.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return None
        for seq in data[0].split():
            status, msg_data = conn.fetch(seq, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
            item = msg_data[0] if msg_data else None
            if status == "OK" and isinstance(item, tuple):
                raw = item[1].decode("utf-8", errors="replace")
                if subject_contains.lower() not in raw.lower():
                    continue
                status, flag_data = conn.fetch(seq, "(FLAGS)")
                if status == "OK" and flag_data and flag_data[0]:
                    raw_flags = flag_data[0]
                    if isinstance(raw_flags, bytes):
                        return raw_flags.decode("utf-8", errors="replace")
                    return str(raw_flags)
        return None
    finally:
        conn.logout()


def _send_and_locate(user_session, app_url, user_account_id):
    subject = f"E2E spam {uuid.uuid4().hex[:8]}"
    r = user_session.post(
        f"{app_url}/app/mail/send",
        data={
            "account_id": user_account_id,
            "to": USER,
            "subject": subject,
            "body_html": f"<p>{subject}</p>",
        },
        allow_redirects=True,
    )
    assert r.status_code == 200
    assert imap_folder_has_message(USER, E2E_DEFAULT_PASSWORD, "INBOX", subject, timeout=30)
    return subject


def _find_message_id(session, app_url, account_id, folder, subject):
    """Locate the folder-view row whose OWN subject title matches.

    Never search the whole page for the subject: every message page carries
    the send-widget's ``data-subject`` of the last compose, which false-
    matches older messages and makes the test act on the wrong row."""
    r = session.get(f"{app_url}/app/mail/folder/{account_id}/{folder}")
    if r.status_code != 200:
        return None
    pattern = re.compile(
        r'data-message-id="(\d+)"(?:(?!data-message-id=).)*?title="' + re.escape(subject) + r'"',
        re.DOTALL,
    )
    m = pattern.search(r.text)
    return m.group(1) if m else None


@skip_if_no_services
class TestReportSpamAction:
    def test_report_spam_moves_to_junk_and_sets_keyword(
        self, app_url, user_session, user_account_id
    ):
        subject = _send_and_locate(user_session, app_url, user_account_id)
        msg_id = wait_for(
            lambda: _find_message_id(user_session, app_url, user_account_id, "INBOX", subject),
            timeout=20,
        )
        assert msg_id is not None, f"Message '{subject}' not found in the folder view"

        r = user_session.post(
            f"{app_url}/app/mail/message/{user_account_id}/{msg_id}/junk",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") == "ok", body

        # Service-level verification: message is in Junk with the Junk keyword.
        assert imap_folder_has_message(
            USER, E2E_DEFAULT_PASSWORD, "Junk", subject_contains=subject, timeout=60
        )

        flags = wait_for(
            lambda: _imap_flags_with_subject(USER, E2E_DEFAULT_PASSWORD, "Junk", subject),
            timeout=20,
        )
        assert "Junk" in flags, f"Junk keyword missing on the moved message: {flags!r}"

    def test_not_spam_moves_back_to_inbox_and_clears_keyword(
        self, app_url, user_session, user_account_id
    ):
        subject = _send_and_locate(user_session, app_url, user_account_id)
        msg_id = wait_for(
            lambda: _find_message_id(user_session, app_url, user_account_id, "INBOX", subject),
            timeout=20,
        )
        assert msg_id is not None

        r = user_session.post(
            f"{app_url}/app/mail/message/{user_account_id}/{msg_id}/junk",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert r.status_code == 200 and r.json().get("status") == "ok"

        # Find the message id again inside the Junk folder view.
        junk_msg_id = wait_for(
            lambda: _find_message_id(user_session, app_url, user_account_id, "Junk", subject),
            timeout=30,
        )
        assert junk_msg_id is not None, "Message not visible in the Junk folder view after move"

        r = user_session.post(
            f"{app_url}/app/mail/message/{user_account_id}/{junk_msg_id}/not-junk",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert r.status_code == 200
        assert r.json().get("status") == "ok", r.json()

        assert imap_folder_has_message(
            USER, E2E_DEFAULT_PASSWORD, "INBOX", subject_contains=subject, timeout=60
        )
        flags = wait_for(
            lambda: _imap_flags_with_subject(USER, E2E_DEFAULT_PASSWORD, "INBOX", subject),
            timeout=20,
        )
        assert "Junk" not in flags, f"Junk keyword still set after not-spam: {flags!r}"

    def test_report_spam_does_not_auto_disable(self, app_url, user_session, user_account_id):
        """The U9.5 toggle must stay on after a successful report — the
        auto-disable loop is the production regression this guards."""
        subject = _send_and_locate(user_session, app_url, user_account_id)
        msg_id = wait_for(
            lambda: _find_message_id(user_session, app_url, user_account_id, "INBOX", subject),
            timeout=20,
        )
        assert msg_id is not None
        r = user_session.post(
            f"{app_url}/app/mail/message/{user_account_id}/{msg_id}/junk",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert r.status_code == 200 and r.json().get("status") == "ok"
        settings = user_session.get(f"{app_url}/app/mail/settings", allow_redirects=True)
        assert settings.status_code == 200
        assert "Temporarily off" not in settings.text


@skip_if_no_services
@pytest.mark.parametrize("folder", ["INBOX", "Junk"])
def test_dovecot_accepts_junk_keyword(folder):
    r"""Direct capability check: the dev Dovecot must accept the `Junk`
    keyword and reject the `\Junk` system flag (the reason the old code
    failed). Guards against a future server change silently breaking the
    spam action again."""
    conn = imap_connect(USER, E2E_DEFAULT_PASSWORD)
    try:
        status, _ = conn.select(folder)
        if status != "OK":
            pytest.skip(f"folder {folder} not selectable")
        status, data = conn.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            pytest.skip(f"no messages in {folder} to probe flags with")
        seq = data[0].split()[0]
        conn.store(seq, "+FLAGS", "(Junk)")
        try:
            conn.store(seq, "-FLAGS", "(Junk)")
        except Exception:
            pytest.fail("clearing the Junk keyword failed on dev Dovecot")
        with pytest.raises(imaplib.IMAP4.error):
            conn.store(seq, "+FLAGS", "(\\Junk)")
    finally:
        conn.logout()
