"""E2E: IMAPSieve spam training against the dev rspamd.

Verifies the dev mirror of the production Dovecot/rspamd wiring (HLD U19.20a):
moving a message to Junk over IMAP trains rspamd as spam, moving it back
trains as ham. Skips unless the dev stack (with the rspamd controller
reachable) is running.
"""

import email.utils
import imaplib
import json
import os
import smtplib
import urllib.error
import urllib.request
import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import E2E_DEFAULT_PASSWORD, wait_for

E2E_USER = "e2e-test@test.localhost"
RSPAMD_URL = os.environ.get("E2E_RSPAMD_URL", "http://127.0.0.1:11334")

# rspamd's bayes classifier refuses messages with fewer tokens than this;
# keep the test body comfortably above it (mirrors rspamd's own minimum).
_BODY = (
    "This end-to-end verification message intentionally carries a longer body "
    "so that the statistical bayes classifier receives more tokens than its "
    "configured minimum threshold and accepts it as valid training material "
    "during the automated imapsieve pipeline test against the dev mail stack."
)


def _rspamd_available() -> bool:
    try:
        with urllib.request.urlopen(f"{RSPAMD_URL}/ping", timeout=3) as r:
            return r.read().strip() == b"pong"
    except (OSError, urllib.error.URLError):
        return False


skip_if_no_rspamd = pytest.mark.skipif(
    not _rspamd_available(),
    reason="rspamd controller not reachable (E2E_RSPAMD_URL); start with: make dev-up",
)


def _learned_count() -> int:
    with urllib.request.urlopen(f"{RSPAMD_URL}/stat", timeout=5) as r:
        return int(json.load(r)["learned"])


def _imap() -> imaplib.IMAP4:
    client = imaplib.IMAP4("localhost", 143)
    client.login(E2E_USER, E2E_DEFAULT_PASSWORD)
    return client


def _uid_for(client: imaplib.IMAP4, folder: str, msg_id: str) -> str | None:
    client.select(folder)
    typ, data = client.uid("SEARCH", "HEADER", "Message-ID", msg_id)
    assert typ == "OK"
    uids = data[0].split()
    return uids[0].decode() if uids else None


def _move(client: imaplib.IMAP4, folder: str, uid: str, destination: str) -> None:
    typ, _ = client.uid("COPY", uid, destination)
    assert typ == "OK"
    client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    client.expunge()


@skip_if_no_services
@skip_if_no_rspamd
class TestImapsieveSpamTraining:
    def test_move_to_junk_learns_spam_and_move_back_learns_ham(self):
        msg_id = f"<e2e-spam-training-{uuid.uuid4().hex}@test.localhost>"
        raw = (
            f"From: sender@test.localhost\r\n"
            f"To: {E2E_USER}\r\n"
            f"Subject: e2e spam training verification {uuid.uuid4().hex[:8]}\r\n"
            f"Date: {email.utils.formatdate(localtime=True)}\r\n"
            f"Message-ID: {msg_id}\r\n"
            f"\r\n"
            f"{_BODY}\r\n"
        ).encode()

        baseline = _learned_count()

        with smtplib.SMTP("localhost", 25, timeout=30) as smtp:
            smtp.sendmail("sender@test.localhost", [E2E_USER], raw)

        client = _imap()
        try:
            uid = wait_for(
                lambda: _uid_for(client, "INBOX", msg_id),
                timeout=30,
                interval=2,
            )
            _move(client, "INBOX", uid, "Junk")
            wait_for(
                lambda: _learned_count() > baseline,
                timeout=20,
                interval=1,
            )

            uid = wait_for(
                lambda: _uid_for(client, "Junk", msg_id),
                timeout=10,
                interval=1,
            )
            before_ham = _learned_count()
            _move(client, "Junk", uid, "INBOX")
            wait_for(
                lambda: _learned_count() > before_ham,
                timeout=20,
                interval=1,
            )

            uid = _uid_for(client, "INBOX", msg_id)
            if uid is not None:
                _move(client, "INBOX", uid, "Trash")
        finally:
            client.logout()
