"""Cross-module cache isolation tests.

Regression guard for the bug where all four cache modules (mail, docs, contacts,
calendar) shared a single ``customer_{cid}_account_{aid}.db`` file and a single
``_schema_migrations`` ledger.  The collision caused one module's
``open_cache`` destructive recovery to wipe another module's tables, and
migration-name collisions (all named ``0001_baseline_schema``) caused baselines
to be silently skipped.

These tests verify the fix: each module gets its own namespaced file path and
its own migration-name prefix, so modules never interfere with each other.
"""

from __future__ import annotations

import os

import sqlcipher3

from app.modules.calendar.services.cache import get_cache_path as calendar_cache_path
from app.modules.calendar.services.cache_db import open_cache as calendar_open_cache
from app.modules.contacts.services.cache import get_cache_path as contacts_cache_path
from app.modules.contacts.services.cache_db import open_cache as contacts_open_cache
from app.modules.docs.services.cache import get_cache_path as docs_cache_path
from app.modules.docs.services.cache_db import clear_cache_schema_memo as docs_clear_memo
from app.modules.docs.services.cache_db import open_cache as docs_open_cache
from app.modules.mail.services.cache import build_cache_path as mail_cache_path
from app.modules.mail.services.cache_db import clear_cache_schema_memo as mail_clear_memo
from app.modules.mail.services.cache_db import list_cached_folders, upsert_folder
from app.modules.mail.services.cache_db import open_cache as mail_open_cache

_KEY = "0" * 64


class _FakeAccount:
    """Minimal stand-in for CustomerAccount — avoids DB setup."""

    def __init__(self, customer_id: int, account_id: int, cache_db_path: str | None = None):
        self.customer_id = customer_id
        self.id = account_id
        self.cache_db_path = cache_db_path


class TestCachePathIsolation:
    """Each module must produce a unique file path for the same account."""

    def test_all_four_paths_differ(self):
        acct = _FakeAccount(customer_id=1, account_id=2)
        paths = {
            mail_cache_path(1, 2),
            docs_cache_path(acct),
            contacts_cache_path(acct),
            calendar_cache_path(acct),
        }
        assert len(paths) == 4, f"paths must be unique, got {paths}"

    def test_mail_path_has_mail_suffix(self):
        assert mail_cache_path(1, 2).endswith("_mail.db")

    def test_docs_path_has_docs_suffix(self):
        acct = _FakeAccount(1, 2)
        assert docs_cache_path(acct).endswith("_docs.db")

    def test_contacts_path_has_contacts_suffix(self):
        acct = _FakeAccount(1, 2)
        assert contacts_cache_path(acct).endswith("_contacts.db")

    def test_calendar_path_has_calendar_suffix(self):
        acct = _FakeAccount(1, 2)
        assert calendar_cache_path(acct).endswith("_calendar.db")

    def test_docs_does_not_read_account_cache_db_path(self):
        """Docs must NOT return account.cache_db_path — it must compute its own."""
        acct = _FakeAccount(1, 2, cache_db_path="/tmp/preset_by_mail.db")
        path = docs_cache_path(acct)
        assert path != "/tmp/preset_by_mail.db"
        assert path.endswith("_docs.db")


class TestMigrationNameIsolation:
    """Migration names must be prefixed per-module so they never collide."""

    def test_mail_migrations_prefixed(self):
        from app.modules.mail.services.cache_migrations import MAIL_CACHE_MIGRATIONS

        for m in MAIL_CACHE_MIGRATIONS:
            assert m.name.startswith("mail_"), f"{m.name} missing mail_ prefix"

    def test_docs_migrations_prefixed(self):
        from app.modules.docs.services.cache_migrations import DOCS_CACHE_MIGRATIONS

        for m in DOCS_CACHE_MIGRATIONS:
            assert m.name.startswith("docs_"), f"{m.name} missing docs_ prefix"

    def test_contacts_migrations_prefixed(self):
        from app.modules.contacts.services.cache_migrations import CONTACTS_CACHE_MIGRATIONS

        for m in CONTACTS_CACHE_MIGRATIONS:
            assert m.name.startswith("contacts_"), f"{m.name} missing contacts_ prefix"

    def test_calendar_migrations_prefixed(self):
        from app.modules.calendar.services.cache_migrations import CALENDAR_CACHE_MIGRATIONS

        for m in CALENDAR_CACHE_MIGRATIONS:
            assert m.name.startswith("calendar_"), f"{m.name} missing calendar_ prefix"

    def test_no_migration_name_collision_across_modules(self):
        from app.modules.calendar.services.cache_migrations import CALENDAR_CACHE_MIGRATIONS
        from app.modules.contacts.services.cache_migrations import CONTACTS_CACHE_MIGRATIONS
        from app.modules.docs.services.cache_migrations import DOCS_CACHE_MIGRATIONS
        from app.modules.mail.services.cache_migrations import MAIL_CACHE_MIGRATIONS

        all_names = set()
        for registry in (
            MAIL_CACHE_MIGRATIONS,
            DOCS_CACHE_MIGRATIONS,
            CONTACTS_CACHE_MIGRATIONS,
            CALENDAR_CACHE_MIGRATIONS,
        ):
            for m in registry:
                assert m.name not in all_names, f"migration name collision: {m.name}"
                all_names.add(m.name)


class TestCrossModuleFileIsolation:
    """Opening one module's cache must not affect another module's cache file.

    This is the core regression test for the bug where docs' ``open_cache``
    deleted the shared file (wiping mail's tables) when its baseline migration
    was skipped due to a name collision in ``_schema_migrations``.
    """

    def test_docs_open_does_not_destroy_mail_cache(self, tmp_path):
        customer_id, account_id = 1, 2
        mail_path = str(tmp_path / f"customer_{customer_id}_account_{account_id}_mail.db")
        docs_path = str(tmp_path / f"customer_{customer_id}_account_{account_id}_docs.db")
        key = _KEY

        # 1. Create and populate the mail cache.
        mail_clear_memo(mail_path)
        conn = mail_open_cache(mail_path, key)
        try:
            upsert_folder(conn, "INBOX", 5)
            upsert_folder(conn, "Sent", 1)
            conn.commit()
        finally:
            conn.close()

        # 2. Open the docs cache (separate file) — must not touch mail's file.
        docs_clear_memo(docs_path)
        docs_conn = docs_open_cache(docs_path, key)
        docs_conn.close()

        # 3. Re-open the mail cache — data must be intact.
        mail_clear_memo(mail_path)
        conn = mail_open_cache(mail_path, key)
        try:
            folders = {r["name"]: r["unread_count"] for r in list_cached_folders(conn)}
            assert folders == {"INBOX": 5, "Sent": 1}
        finally:
            conn.close()
            mail_clear_memo(mail_path)
            docs_clear_memo(docs_path)

    def test_all_four_caches_coexist(self, tmp_path):
        """All four module caches for the same account can coexist without interference."""
        customer_id, account_id = 1, 2
        key = _KEY

        paths = {
            "mail": str(tmp_path / f"customer_{customer_id}_account_{account_id}_mail.db"),
            "docs": str(tmp_path / f"customer_{customer_id}_account_{account_id}_docs.db"),
            "contacts": str(tmp_path / f"customer_{customer_id}_account_{account_id}_contacts.db"),
            "calendar": str(tmp_path / f"customer_{customer_id}_account_{account_id}_calendar.db"),
        }

        # Open each cache and write a row, verifying tables exist.
        mail_clear_memo(paths["mail"])
        conn = mail_open_cache(paths["mail"], key)
        try:
            upsert_folder(conn, "INBOX", 3)
            conn.commit()
        finally:
            conn.close()

        docs_clear_memo(paths["docs"])
        conn = docs_open_cache(paths["docs"], key)
        try:
            from app.modules.docs.services.cache_db import create_document

            create_document(conn, "doc-1", "Test", "odt", account_id)
            conn.commit()
        finally:
            conn.close()

        conn = contacts_open_cache(paths["contacts"], key)
        try:
            conn.execute("INSERT INTO contacts (uid, fn) VALUES ('test-uid', 'Test Contact')")
            conn.commit()
        finally:
            conn.close()

        conn = calendar_open_cache(paths["calendar"], key)
        try:
            conn.execute(
                "INSERT INTO calendars (uid, href, displayname) VALUES ('cal-1', '/cal/1/', 'Test')"
            )
            conn.commit()
        finally:
            conn.close()

        # Re-open mail cache — verify its data survived the other modules opening.
        mail_clear_memo(paths["mail"])
        conn = mail_open_cache(paths["mail"], key)
        try:
            folders = {r["name"]: r["unread_count"] for r in list_cached_folders(conn)}
            assert folders == {"INBOX": 3}
        finally:
            conn.close()

        # Verify each file exists and has the expected table.
        for module, expected_table in [
            ("mail", "folders"),
            ("docs", "documents"),
            ("contacts", "contacts"),
            ("calendar", "calendars"),
        ]:
            assert os.path.exists(paths[module]), f"{module} cache file missing"
            conn = sqlcipher3.connect(paths[module])
            conn.execute(f"PRAGMA key = \"x'{key}'\"")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            conn.close()
            assert expected_table in tables, f"{module} cache missing {expected_table} table"

        # Cleanup
        for p in paths.values():
            mail_clear_memo(p)
            docs_clear_memo(p)


class TestOldSharedFilePathStillWorks:
    """Old shared cache files (pre-fix) are simply orphaned — they don't break
    the new namespaced caches.
    """

    def test_old_shared_file_does_not_interfere(self, tmp_path):
        customer_id, account_id = 1, 2
        key = _KEY

        # Simulate an old shared cache file (pre-fix naming, no suffix).
        old_path = str(tmp_path / f"customer_{customer_id}_account_{account_id}.db")
        old_conn = sqlcipher3.connect(old_path)
        old_conn.execute(f"PRAGMA key = \"x'{key}'\"")
        old_conn.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY, name TEXT)")
        old_conn.commit()
        old_conn.close()

        # New mail cache path (different file).
        mail_path = str(tmp_path / f"customer_{customer_id}_account_{account_id}_mail.db")
        mail_clear_memo(mail_path)
        conn = mail_open_cache(mail_path, key)
        try:
            upsert_folder(conn, "INBOX", 0)
            conn.commit()
            folders = {r["name"]: r["unread_count"] for r in list_cached_folders(conn)}
            assert folders == {"INBOX": 0}
        finally:
            conn.close()
            mail_clear_memo(mail_path)

        # Old file still exists (orphaned, not deleted).
        assert os.path.exists(old_path)
