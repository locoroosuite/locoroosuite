from unittest.mock import patch

import pytest
import sqlcipher3

from app.modules.mail.services import cache_db


def _make_legacy_cache(db_path, key="0" * 64):
    """Build a cache DB whose `folders` table predates the unread_count column."""
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    conn.execute("INSERT INTO folders(name) VALUES ('INBOX')")
    conn.commit()
    conn.close()


def _make_legacy_cache_no_unique(db_path, key="0" * 64):
    """Build a cache DB whose `folders.name` predates any UNIQUE constraint.

    This is the shape that broke ``upsert_folder``'s ``ON CONFLICT(name)``:
    ``CREATE TABLE IF NOT EXISTS`` never back-patches an existing table, so a
    DB created before ``name`` was unique kept failing every folder sync.
    """
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO folders(name) VALUES ('INBOX')")
    conn.commit()
    conn.close()


def _make_legacy_cache_with_account_id(db_path, key="0" * 64):
    """Build a cache DB whose `folders` table has a stale ``account_id NOT NULL``.

    This is the shape that broke every folder sync with
    ``IntegrityError: NOT NULL constraint failed: folders.account_id``: a
    previous schema version added the column, the current code never
    populates it, and ``CREATE TABLE IF NOT EXISTS`` cannot remove it.
    """
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute(
        "CREATE TABLE folders ("
        "id INTEGER PRIMARY KEY, "
        "name TEXT UNIQUE, "
        "account_id INTEGER NOT NULL, "
        "unread_count INTEGER DEFAULT 0"
        ")"
    )
    conn.execute("INSERT INTO folders(name, account_id, unread_count) VALUES ('INBOX', 2, 3)")
    conn.execute("INSERT INTO folders(name, account_id, unread_count) VALUES ('Sent', 2, 0)")
    conn.commit()
    conn.close()


class TestLegacyCacheMigration:
    def test_legacy_folders_table_gets_unread_count_on_open(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        key = "0" * 64
        _make_legacy_cache(db_path, key)
        cache_db.clear_cache_schema_memo(db_path)

        with patch.object(cache_db, "init_cache_schema", wraps=cache_db.init_cache_schema) as spy:
            conn = cache_db.open_cache(db_path, key)
            assert spy.called

        cols = [row[1] for row in conn.execute("PRAGMA table_info(folders)").fetchall()]
        assert "unread_count" in cols
        folders = {r["name"]: r["unread_count"] for r in cache_db.list_cached_folders(conn)}
        assert folders == {"INBOX": 0}
        conn.close()
        cache_db.clear_cache_schema_memo(db_path)

    def test_legacy_folders_without_unique_gets_index_on_open(self, tmp_path):
        db_path = str(tmp_path / "legacy_no_unique.db")
        key = "0" * 64
        _make_legacy_cache_no_unique(db_path, key)
        cache_db.clear_cache_schema_memo(db_path)

        conn = cache_db.open_cache(db_path, key)
        try:
            # Regression: previously raised
            #   OperationalError: ON CONFLICT clause does not match any
            #   PRIMARY KEY or UNIQUE constraint
            # because the legacy folders.name column had no unique constraint.
            cache_db.upsert_folder(conn, "INBOX", 2)
            cache_db.upsert_folder(conn, "Sent", 0)
            folders = {r["name"]: r["unread_count"] for r in cache_db.list_cached_folders(conn)}
            assert folders == {"INBOX": 2, "Sent": 0}

            # Re-upserting the same name updates in place rather than inserting.
            cache_db.upsert_folder(conn, "INBOX", 5)
            folders = {r["name"]: r["unread_count"] for r in cache_db.list_cached_folders(conn)}
            assert folders == {"INBOX": 5, "Sent": 0}

            # Uniqueness is now actually enforced by the migrated index.
            with pytest.raises(sqlcipher3.dbapi2.IntegrityError):
                conn.execute("INSERT INTO folders(name, unread_count) VALUES ('INBOX', 9)")
                conn.commit()
        finally:
            conn.close()
            cache_db.clear_cache_schema_memo(db_path)

    def test_second_open_does_not_recheck_schema(self, tmp_path):
        db_path = str(tmp_path / "legacy2.db")
        key = "0" * 64
        _make_legacy_cache(db_path, key)
        cache_db.clear_cache_schema_memo(db_path)

        conn = cache_db.open_cache(db_path, key)
        conn.close()

        with patch.object(cache_db, "init_cache_schema", wraps=cache_db.init_cache_schema) as spy:
            conn2 = cache_db.open_cache(db_path, key)
            assert not spy.called
        conn2.close()
        cache_db.clear_cache_schema_memo(db_path)

    def test_clear_memo_forces_resync_after_recreation(self, tmp_path):
        db_path = str(tmp_path / "legacy3.db")
        key = "0" * 64
        _make_legacy_cache(db_path, key)
        cache_db.clear_cache_schema_memo(db_path)

        conn = cache_db.open_cache(db_path, key)
        conn.close()
        assert db_path in cache_db._SCHEMA_INITIALIZED

        cache_db.clear_cache_schema_memo(db_path)
        assert db_path not in cache_db._SCHEMA_INITIALIZED

        with patch.object(cache_db, "init_cache_schema", wraps=cache_db.init_cache_schema) as spy:
            conn = cache_db.open_cache(db_path, key)
            assert spy.called
        conn.close()
        cache_db.clear_cache_schema_memo(db_path)


class TestFoldersDropAccountId:
    """Regression tests for the ``folders.account_id NOT NULL`` bug.

    The stale column caused every sync to fail with
    ``IntegrityError: NOT NULL constraint failed: folders.account_id``,
    surfacing as two IMAP-unavailable warnings (INBOX + Sent) in the UI.
    """

    def test_account_id_column_dropped_on_open(self, tmp_path):
        db_path = str(tmp_path / "stale_account_id.db")
        key = "0" * 64
        _make_legacy_cache_with_account_id(db_path, key)
        cache_db.clear_cache_schema_memo(db_path)

        conn = cache_db.open_cache(db_path, key)
        try:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(folders)").fetchall()]
            assert "account_id" not in cols
            assert "name" in cols
            assert "unread_count" in cols
        finally:
            conn.close()
            cache_db.clear_cache_schema_memo(db_path)

    def test_folder_data_preserved_after_rebuild(self, tmp_path):
        db_path = str(tmp_path / "stale_account_id_data.db")
        key = "0" * 64
        _make_legacy_cache_with_account_id(db_path, key)
        cache_db.clear_cache_schema_memo(db_path)

        conn = cache_db.open_cache(db_path, key)
        try:
            folders = {r["name"]: r["unread_count"] for r in cache_db.list_cached_folders(conn)}
            assert folders == {"INBOX": 3, "Sent": 0}
        finally:
            conn.close()
            cache_db.clear_cache_schema_memo(db_path)

    def test_upsert_folder_works_after_migration(self, tmp_path):
        """The original failing operation: inserting a folder row."""
        db_path = str(tmp_path / "stale_account_id_upsert.db")
        key = "0" * 64
        _make_legacy_cache_with_account_id(db_path, key)
        cache_db.clear_cache_schema_memo(db_path)

        conn = cache_db.open_cache(db_path, key)
        try:
            # Previously raised IntegrityError: NOT NULL constraint failed
            cache_db.upsert_folder(conn, "Drafts", 1)
            cache_db.upsert_folder(conn, "INBOX", 5)
            folders = {r["name"]: r["unread_count"] for r in cache_db.list_cached_folders(conn)}
            assert folders == {"INBOX": 5, "Sent": 0, "Drafts": 1}
        finally:
            conn.close()
            cache_db.clear_cache_schema_memo(db_path)


class TestErrorPageResetButton:
    def test_error_page_shows_reset_button_with_account(self, app):
        from flask import render_template
        with app.test_request_context("/", headers={"Accept": "text/html"}):
            html = render_template(
                "error.html",
                title="Something went wrong",
                message="An unexpected error occurred.",
                show_cache_reset=True,
                account_id=42,
            )
        assert "Reset cache" in html
        assert "reset-cache/42" in html
        assert "Back to login" in html

    def test_error_page_hides_reset_button_without_account(self, app):
        from flask import render_template
        with app.test_request_context("/", headers={"Accept": "text/html"}):
            html = render_template(
                "error.html",
                title="Something went wrong",
                message="An unexpected error occurred.",
            )
        assert "Reset cache" not in html
        assert "Back to login" in html
