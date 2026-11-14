"""Adversarial QA fixtures for the 2026-11-14 MCP issue-fix review.

Reuses the tests/api fixtures (api_customer / setup_cache_db / token
helpers) and adds a *stateful* fake IMAP server so draft lifecycle attacks
observe real append/delete accounting instead of scripted one-shot mocks:

- APPEND stores the raw message and mints a real APPENDUID
- UID SEARCH reflects the actual folder contents (per-folder UID space)
- UID STORE + EXPUNGE really remove messages (unless ``ignore_deletes``
  is set, which simulates a server whose deletes silently do nothing)
"""

import contextlib
import email
import imaplib
import re
import types
from unittest.mock import patch

import pytest

from tests.api.conftest import (
    auth_header,  # noqa: F401 — re-exported for the attack modules
    cleanup_cache_db,
    create_api_token,
    setup_cache_db,
)

_IMAP = "app.modules.mail.services.imap_client"
_SMTP = "app.modules.mail.services.smtp_client"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Same as tests/api/conftest: the rate limiter keys on token DB id, and
    ids reset to 1 after every _clean_db wipe, so without this the whole
    qa_review suite shares one 60/min bucket and late tests get 429s."""
    from app.api.controllers import helpers as _helpers

    _helpers._rate_limit_store.clear()
    yield


class FakeImapServer:
    def __init__(self, folders=("INBOX", "Drafts", "Sent")):
        # folder -> {uid(str): {"raw": bytes, "flags": str, "deleted": bool}}
        self.messages: dict[str, dict] = {name: {} for name in folders}
        self._next_uid = 700
        self.append_calls: list[tuple[str, str | None, bytes]] = []
        self.store_calls: list[str] = []
        self.expunge_calls = 0
        # When True, STORE/EXPUNGE "succeed" but never remove anything —
        # the "UID still present after delete" phantom-success repro.
        self.ignore_deletes = False

    # -- server-side state helpers for assertions -----------------------
    def uids(self, folder):
        return sorted(self.messages.get(folder, {}), key=int)

    def uid_present(self, folder, uid) -> bool:
        return str(uid) in self.messages.get(folder, {})

    def raw_for(self, folder, uid) -> bytes | None:
        entry = self.messages.get(folder, {}).get(str(uid))
        return entry["raw"] if entry else None

    def append_count(self, folder=None) -> int:
        if folder is None:
            return len(self.append_calls)
        return sum(1 for f, _flags, _raw in self.append_calls if f == folder)

    def seed_message(self, folder, uid, raw=b"", flags=""):
        self.messages.setdefault(folder, {})[str(uid)] = {
            "raw": raw,
            "flags": flags,
            "deleted": False,
        }

    # -- client factory ---------------------------------------------------
    def client(self):
        return _FakeClient(self)


class _FakeClient:
    def __init__(self, server: FakeImapServer):
        self.server = server
        self.selected: str | None = None

    @staticmethod
    def _quote(name):
        return name

    def append(self, folder, flags, date_time, raw):
        if folder not in self.server.messages:
            raise imaplib.IMAP4.error(f"APPEND failed: no mailbox {folder}")
        uid = str(self.server._next_uid)
        self.server._next_uid += 1
        self.server.messages[folder][uid] = {
            "raw": raw,
            "flags": flags or "",
            "deleted": False,
        }
        self.server.append_calls.append((folder, flags, raw))
        return ("OK", [f"[APPENDUID 1 {uid}]".encode()])

    def select(self, folder):
        if folder not in self.server.messages:
            return ("NO", [b"no such mailbox"])
        self.selected = folder
        return ("OK", [str(len(self.server.messages[folder])).encode()])

    def uid(self, command, *args):
        if command == "SEARCH":
            criteria = args[-1] if args and isinstance(args[-1], str) else "ALL"
            match = re.match(r"^UID\s+(\S+)$", criteria.strip())
            present = set(self.server.messages.get(self.selected or "", {}))
            if match:
                wanted = match.group(1)
                hits = [wanted] if wanted in present else []
            else:
                hits = sorted(present, key=int)
            return ("OK", [b" ".join(h.encode() for h in hits)])
        if command == "STORE":
            # args: (uid, "+FLAGS", "(\\Deleted)")
            self.server.store_calls.append(str(args[0]))
            folder_msgs = self.server.messages.get(self.selected or "", {})
            entry = folder_msgs.get(str(args[0]))
            if entry is not None and "+FLAGS" in str(args[1]):
                entry["deleted"] = True
            return ("OK", [b""])
        if command == "FETCH":
            entry = self.server.messages.get(self.selected or "", {}).get(str(args[0]))
            if entry is None:
                return ("OK", [b""])
            meta = f"{args[0]} (UID {args[0]} FLAGS ({entry['flags']}))".encode()
            return ("OK", [(meta, entry["raw"])])
        if command == "COPY":
            return ("OK", [b""])
        return ("OK", [b""])

    def expunge(self):
        self.server.expunge_calls += 1
        if not self.server.ignore_deletes:
            folder_msgs = self.server.messages.get(self.selected or "", {})
            for uid in [u for u, e in folder_msgs.items() if e.get("deleted")]:
                del folder_msgs[uid]
        return ("OK", [b""])

    def create(self, folder):
        self.server.messages.setdefault(folder, {})
        return ("OK", [b""])

    def list(self):
        return ("OK", [f'"|" {name}'.encode() for name in self.server.messages])

    def logout(self):
        return ("BYE", [b""])


@contextlib.contextmanager
def fake_imap(server: FakeImapServer, *, cache_fetch: bool = True):
    """Patch the IMAP layer onto ``server`` for the duration of the block.

    ``cache_fetch=True`` makes fetch_and_cache_message return the actual
    appended message, so saved drafts/sent copies land in the cache the
    same way the real sync path does.
    """

    def _fetch_with_flags(client, uid):
        entry = server.messages.get(client.selected, {}).get(str(uid))
        if entry is None:
            return (None, [], None)
        return (email.message_from_bytes(entry["raw"]), [], None)

    def _fetch_raw(client, uid):
        entry = server.messages.get(client.selected, {}).get(str(uid))
        return entry["raw"] if entry else None

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(f"{_IMAP}.connect_imap", side_effect=lambda *a, **k: server.client())
        )
        stack.enter_context(patch(f"{_IMAP}.login_imap"))
        if cache_fetch:
            stack.enter_context(
                patch(f"{_IMAP}.fetch_message_with_flags", side_effect=_fetch_with_flags)
            )
            stack.enter_context(patch(f"{_IMAP}.fetch_raw_message", side_effect=_fetch_raw))
        yield


@contextlib.contextmanager
def fake_smtp(sent: list):
    """Patch SMTP; every sent message's bytes land in ``sent``."""

    def _record(_server, from_addr, recipients, msg_bytes):
        sent.append({"from": from_addr, "recipients": recipients, "raw": msg_bytes})

    with (
        patch(f"{_SMTP}.smtp_connect", return_value=None),
        patch(f"{_SMTP}.smtp_login"),
        patch(f"{_SMTP}.smtp_send", side_effect=_record),
    ):
        yield


class MailEnv(types.SimpleNamespace):
    """Bundle returned by the qa_mail fixtures."""


def _make_customer(app, client, email_addr, domain_name):
    """Create user + domain + account + API token + temp cache DB."""
    from app.shared.db import db as _db
    from app.shared.keys import set_user_key
    from app.shared.models.core import CustomerAccount, Domain, User

    with app.app_context():
        user = User()
        user.email = email_addr
        user.role = "customer"
        user.is_active = True
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()

        domain = Domain()
        domain.name = domain_name
        domain.is_active = True
        domain.status = "active"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.imap_tls = True
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        _db.session.add(domain)
        _db.session.flush()

        account = CustomerAccount()
        account.customer_id = user.id
        account.domain_id = domain.id
        account.email_address = email_addr
        account.auth_type = "password"
        account.username = email_addr
        account.cache_db_path = ""
        account.api_enabled = True
        _db.session.add(account)
        _db.session.commit()
        user_id, account_id = user.id, account.id

        from app.modules.mail.services.secrets import encrypt_with_key

        account.encrypted_secret = encrypt_with_key("testpass", "a" * 64)
        _db.session.commit()

        token_value, _ = create_api_token(app, user_id)

    set_user_key(user_id, "0" * 64)
    cache_path = setup_cache_db(app, account_id)
    return MailEnv(
        client=client,
        token=token_value,
        user_id=user_id,
        account_id=account_id,
        cache_path=cache_path,
        server=FakeImapServer(),
    )


@pytest.fixture()
def api_customer(app, client, _clean_db):
    """Local copy of tests/api/conftest.api_customer (conftests do not leak
    across directories)."""
    from app.shared.db import db as _db
    from app.shared.keys import set_user_key
    from app.shared.models.core import CustomerAccount, Domain, User

    with app.app_context():
        user = User()
        user.email = "qa@example.com"
        user.role = "customer"
        user.is_active = True
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()

        domain = Domain()
        domain.name = "example.com"
        domain.is_active = True
        domain.status = "active"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.imap_tls = True
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        _db.session.add(domain)
        _db.session.flush()

        account = CustomerAccount()
        account.customer_id = user.id
        account.domain_id = domain.id
        account.email_address = "qa@example.com"
        account.auth_type = "password"
        account.username = "qa@example.com"
        account.cache_db_path = ""
        account.api_enabled = True
        _db.session.add(account)
        _db.session.commit()
        user_id, account_id = user.id, account.id

    set_user_key(user_id, "0" * 64)
    yield client, user_id, account_id
    from app.shared.keys import clear_user_key

    clear_user_key(user_id)


@pytest.fixture()
def qa_mail(app, api_customer):
    """One customer, token, temp mail cache DB, and a fake IMAP server."""
    client, user_id, account_id = api_customer
    with app.app_context():
        from app.modules.mail.services.secrets import encrypt_with_key
        from app.shared.db import db as _db
        from app.shared.models.core import CustomerAccount

        account = _db.session.get(CustomerAccount, account_id)
        assert account is not None
        account.encrypted_secret = encrypt_with_key("testpass", "a" * 64)
        _db.session.commit()
        token_value, _ = create_api_token(app, user_id)
    cache_path = setup_cache_db(app, account_id)
    server = FakeImapServer()
    env = MailEnv(
        client=client,
        token=token_value,
        user_id=user_id,
        account_id=account_id,
        cache_path=cache_path,
        server=server,
    )
    yield env
    cleanup_cache_db(cache_path)


@pytest.fixture()
def qa_mail_pair(app, client, _clean_db):
    """Two isolated customers (for cross-customer leak checks)."""
    env1 = _make_customer(app, client, "alpha@qa1.example.com", "qa1.example.com")
    env2 = _make_customer(app, client, "beta@qa2.example.com", "qa2.example.com")
    yield env1, env2
    from app.shared.keys import clear_user_key

    cleanup_cache_db(env1.cache_path)
    cleanup_cache_db(env2.cache_path)
    clear_user_key(env1.user_id)
    clear_user_key(env2.user_id)


def seed_cache_message(
    cache_path,
    *,
    uid,
    folder,
    subject="Seeded",
    sender="s@example.com",
    recipients="r@example.com",
    message_id="<seeded@example.com>",
    in_reply_to=None,
    ref_list=None,
    thread_id: str | None = "__auto__",
    flags=None,
    snippet="seeded snippet",
    body="seeded body",
    body_html="",
):
    """Insert a message row into the mail cache; returns its cache id."""
    from app.modules.mail.services.cache_db import open_cache, upsert_message

    conn = open_cache(cache_path, "a" * 64)
    try:
        upsert_message(
            conn,
            uid=str(uid),
            folder=folder,
            subject=subject,
            sender=sender,
            recipients=recipients,
            date="Tue, 20 May 2026 10:00:00 +0000",
            flags=flags if flags is not None else ["\\Seen"],
            snippet=snippet,
            body=body,
            has_attachments=False,
            message_id=message_id,
            thread_id=None if thread_id == "__auto__" else thread_id,
            in_reply_to=in_reply_to,
            ref_list=ref_list,
            body_html=body_html,
        )
        row = conn.execute(
            "SELECT id FROM messages WHERE uid = ? AND folder = ?", (str(uid), folder)
        ).fetchone()
        assert row is not None
        return row["id"]
    finally:
        conn.close()


def drafts_in_cache(cache_path):
    """All cache rows in any drafts-canonical folder."""
    from app.modules.mail.services.cache_db import open_cache
    from app.modules.mail.services.folder_aliases import canonical_folder_key

    conn = open_cache(cache_path, "a" * 64)
    try:
        rows = conn.execute("SELECT id, uid, folder, subject FROM messages").fetchall()
        return [dict(r) for r in rows if canonical_folder_key(r["folder"] or "") == "drafts"]
    finally:
        conn.close()
