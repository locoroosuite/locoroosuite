"""Produce the known e2e baseline (e2e strategy P5).

Run via `make e2e-reset`. Idempotent: safe to run at any time against the
dev stack. Leaves the environment in this state:

- mail domains test.localhost / dev.test registered with mail-api
- e2e users recreated from scratch (mail-api + app accounts, empty caches)
- IMAP standard folders emptied, CardDAV address books emptied,
  CalDAV events deleted (radicale collections survive user deletion,
  so stale data must be wiped after recreation)
- admin customer 2 purged (test_admin leftover, mirrors the pytest
  session fixture)
- both users verified IMAP-loginable and app-loginable

`make e2e-test` then runs the suite with E2E_BASELINE=1: the pytest
session fixture skips fixture-time provisioning entirely (that provisioning
racing IMAP readiness was a flake source) and only verifies the baseline.
"""

import contextlib
import imaplib
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.e2e.services import (
    APP_URL,
    CALDAV_URL,
    E2E_TEST_USERS,
    admin_session,
    caldav_get_calendars,
    caldav_get_events,
    cleanup_e2e_contacts,
    cleanup_e2e_users,
    ensure_mail_domains,
    imap_connect,
    login_session,
    setup_e2e_users,
)

IMAP_WIPE_FOLDERS = ("INBOX", "Sent", "Drafts", "Trash", "Junk")


def imap_wipe_folders(user: str, password: str) -> list[str]:
    """Empty the standard IMAP folders (best-effort per folder)."""
    wiped: list[str] = []
    conn = imap_connect(user, password)
    try:
        for folder in IMAP_WIPE_FOLDERS:
            try:
                status, _ = conn.select(folder)
            except (imaplib.IMAP4.error, OSError):
                continue
            if status != "OK":
                continue
            status, data = conn.search(None, "ALL")
            if status == "OK" and data and data[0]:
                ids = data[0].split()
                id_list = ",".join(i.decode() for i in ids)
                conn.store(id_list, "+FLAGS.SILENT", "\\Deleted")
                conn.expunge()
                wiped.append(f"{folder}({len(ids)})")
    finally:
        conn.logout()
    return wiped


def caldav_wipe_events(user: str, password: str) -> int:
    """Delete every VEVENT in every calendar of the user."""
    deleted = 0
    for cal in caldav_get_calendars(user, password):
        href = cal.get("href")
        if not href or "VEVENT" not in cal.get("resourcetype_types", []):
            continue
        for event in caldav_get_events(user, href, password):
            event_href = event.get("href")
            if not event_href:
                continue
            r = requests.delete(f"{CALDAV_URL}{event_href}", auth=(user, password), timeout=5)
            if r.status_code in (200, 204, 404):
                deleted += 1
    return deleted


def main() -> int:
    print("==> ensuring mail domains")
    ensure_mail_domains()

    print("==> cleaning previous e2e state (users, contacts, admin leftovers)")
    for email in E2E_TEST_USERS:
        with contextlib.suppress(Exception):
            cleanup_e2e_contacts(email)
    with contextlib.suppress(Exception):
        admin = admin_session()
        admin.post(f"{APP_URL}/admin/customers/2/purge", allow_redirects=True)
    cleanup_e2e_users()

    print("==> recreating e2e users (waits for IMAP login readiness)")
    setup_e2e_users(APP_URL)

    print("==> wiping per-user data (IMAP folders, CalDAV events)")
    for email, password in E2E_TEST_USERS.items():
        wiped = imap_wipe_folders(email, password)
        events = caldav_wipe_events(email, password)
        print(f"    {email}: folders={wiped or 'empty'} caldav_events_deleted={events}")

    print("==> verifying app login")
    for email, password in E2E_TEST_USERS.items():
        login_session(email, password)
        print(f"    {email}: ok")

    print("==> baseline ready (E2E_BASELINE=1 pytest tests/e2e/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
