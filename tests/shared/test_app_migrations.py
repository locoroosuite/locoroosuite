"""Tests for the main application database migration system.

Verifies that the app factory runs the unified migration runner against the
main DB and that all migrations are recorded in ``_schema_migrations``.
"""

import sqlite3

from app.shared.app_migrations import APP_DB_MIGRATIONS
from app.shared.db import db
from app.shared.migrations import run_migrations, table_columns


class TestAppDbMigrations:
    def test_schema_migrations_table_exists_after_factory(self, app):
        with app.app_context():
            conn = db.engine.raw_connection()
            try:
                rows = conn.execute("SELECT name FROM _schema_migrations ORDER BY name").fetchall()
            finally:
                conn.close()
            names = [row[0] for row in rows]
        assert len(names) == len(APP_DB_MIGRATIONS)
        assert names == [m.name for m in APP_DB_MIGRATIONS]

    def test_all_migrations_are_self_guarding(self, app):
        """Running the migration chain a second time is a no-op."""
        with app.app_context():
            conn = db.engine.raw_connection()
            try:
                from app.shared.migrations import run_migrations

                applied = run_migrations(conn, APP_DB_MIGRATIONS)
            finally:
                conn.close()
        assert applied == 0

    def test_domain_status_column_exists(self, app):
        """Spot-check: the domain_status migration (with backfill) ran."""
        with app.app_context():
            conn = db.engine.raw_connection()
            try:
                cols = table_columns(conn, "domains")
            finally:
                conn.close()
        assert "status" in cols

    def test_user_totp_columns_exist(self, app):
        """Spot-check: the last migration in the chain ran."""
        with app.app_context():
            conn = db.engine.raw_connection()
            try:
                cols = table_columns(conn, "users")
            finally:
                conn.close()
        assert "totp_secret" in cols
        assert "totp_enabled" in cols
        assert "backup_codes" in cols


class TestDomainMatrixMasMigration:
    """0018_domain_matrix_mas: MAS config columns replace the shared secret."""

    def _pre_migration_conn(self) -> sqlite3.Connection:
        """A domains table in the pre-0018 shape (shared secret, no MAS)."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE domains (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255),
                matrix_host VARCHAR(255),
                matrix_port INTEGER DEFAULT 8008,
                matrix_use_tls BOOLEAN DEFAULT 0,
                matrix_shared_secret VARCHAR(255)
            )
            """
        )
        conn.execute(
            "INSERT INTO domains (name, matrix_host, matrix_shared_secret)"
            " VALUES ('example.com', 'synapse', 'old-secret')"
        )
        conn.commit()
        return conn

    def _migration_0018(self):
        return next(m for m in APP_DB_MIGRATIONS if m.name == "0018_domain_matrix_mas")

    def test_migration_adds_mas_columns_and_drops_shared_secret(self):
        conn = self._pre_migration_conn()
        try:
            # The minimal pre-schema only carries the matrix columns, so run
            # the 0018 migration directly (the full chain is covered above).
            applied = run_migrations(conn, [self._migration_0018()])
            assert applied == 1
            cols = table_columns(conn, "domains")
            assert "matrix_mas_url" in cols
            assert "matrix_mas_client_id" in cols
            assert "matrix_mas_client_secret" in cols
            assert "chat_visible_domain_ids" in cols
            assert "matrix_shared_secret" not in cols
            # Existing rows survive the column drop.
            row = conn.execute("SELECT name FROM domains").fetchone()
            assert row == ("example.com",)
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        conn = self._pre_migration_conn()
        try:
            run_migrations(conn, [self._migration_0018()])
            assert run_migrations(conn, [self._migration_0018()]) == 0
        finally:
            conn.close()


class TestNotifyChatEnabledMigration:
    """0020_notify_chat_enabled: chat push category toggle (HLD U25.61/U24.27)."""

    def _pre_migration_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE customer_settings (customer_id INTEGER PRIMARY KEY, timezone VARCHAR(64))"
        )
        conn.commit()
        return conn

    def _migration_0020(self):
        return next(m for m in APP_DB_MIGRATIONS if m.name == "0020_notify_chat_enabled")

    def test_migration_adds_column_default_on(self):
        conn = self._pre_migration_conn()
        try:
            assert run_migrations(conn, [self._migration_0020()]) == 1
            cols = table_columns(conn, "customer_settings")
            assert "notify_chat_enabled" in cols
            conn.execute("INSERT INTO customer_settings (customer_id) VALUES (1)")
            value = conn.execute(
                "SELECT notify_chat_enabled FROM customer_settings WHERE customer_id = 1"
            ).fetchone()[0]
            assert value == 1  # default on: push-armed users get chat notifications
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        conn = self._pre_migration_conn()
        try:
            run_migrations(conn, [self._migration_0020()])
            assert run_migrations(conn, [self._migration_0020()]) == 0
        finally:
            conn.close()


class TestCustomerSettingsBrowserTzMigration:
    """0021_customer_settings_browser_tz: browser tz cache for workers (U24.31)."""

    def _pre_migration_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE customer_settings (customer_id INTEGER PRIMARY KEY, timezone VARCHAR(64))"
        )
        conn.commit()
        return conn

    def _migration_0021(self):
        return next(m for m in APP_DB_MIGRATIONS if m.name == "0021_customer_settings_browser_tz")

    def test_migration_adds_column_and_preserves_rows(self):
        conn = self._pre_migration_conn()
        try:
            conn.execute(
                "INSERT INTO customer_settings (customer_id, timezone) VALUES (1, 'browser')"
            )
            conn.commit()
            assert run_migrations(conn, [self._migration_0021()]) == 1
            cols = table_columns(conn, "customer_settings")
            assert "browser_tz" in cols
            row = conn.execute(
                "SELECT timezone FROM customer_settings WHERE customer_id = 1"
            ).fetchone()
            assert row == ("browser",)
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        conn = self._pre_migration_conn()
        try:
            run_migrations(conn, [self._migration_0021()])
            assert run_migrations(conn, [self._migration_0021()]) == 0
        finally:
            conn.close()


class TestSpamPrefsAutoOffMigration:
    """0022_customer_settings_spam_prefs_auto_off: legacy `false` -> `auto_off` (U9.5)."""

    def _pre_migration_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE customer_settings ("
            "customer_id INTEGER PRIMARY KEY, spam_action_prefs TEXT)"
        )
        conn.commit()
        return conn

    def _migration_0022(self):
        return next(
            m for m in APP_DB_MIGRATIONS if m.name == "0022_customer_settings_spam_prefs_auto_off"
        )

    def test_legacy_false_becomes_auto_off_and_other_values_untouched(self):
        import json

        conn = self._pre_migration_conn()
        try:
            conn.executemany(
                "INSERT INTO customer_settings (customer_id, spam_action_prefs) VALUES (?, ?)",
                [
                    (1, json.dumps({"5": False, "6": True, "7": "user_off"})),
                    (2, "{not json"),
                    (3, None),
                    (4, json.dumps({"8": "auto_off"})),
                ],
            )
            conn.commit()
            assert run_migrations(conn, [self._migration_0022()]) == 1
            rows = dict(
                conn.execute(
                    "SELECT customer_id, spam_action_prefs FROM customer_settings"
                ).fetchall()
            )
            assert json.loads(rows[1]) == {"5": "auto_off", "6": True, "7": "user_off"}
            assert rows[2] == "{not json"  # invalid JSON left alone
            assert rows[3] is None  # NULL left alone
            assert json.loads(rows[4]) == {"8": "auto_off"}
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        import json

        conn = self._pre_migration_conn()
        try:
            conn.execute(
                "INSERT INTO customer_settings (customer_id, spam_action_prefs) VALUES (1, ?)",
                (json.dumps({"5": False}),),
            )
            conn.commit()
            run_migrations(conn, [self._migration_0022()])
            assert run_migrations(conn, [self._migration_0022()]) == 0
        finally:
            conn.close()

    def test_migration_skips_when_column_missing(self):
        """The runner records the migration but the guard leaves the table alone."""
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE customer_settings (customer_id INTEGER PRIMARY KEY)")
            conn.commit()
            run_migrations(conn, [self._migration_0022()])
            assert table_columns(conn, "customer_settings") == {"customer_id"}
            assert run_migrations(conn, [self._migration_0022()]) == 0
        finally:
            conn.close()


class TestCustomerSettingsTextScaleMigration:
    """0023_customer_settings_text_scale: per-user text scale (HLD UX8c)."""

    def _pre_migration_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE customer_settings (customer_id INTEGER PRIMARY KEY, timezone VARCHAR(64))"
        )
        conn.commit()
        return conn

    def _migration_0023(self):
        return next(m for m in APP_DB_MIGRATIONS if m.name == "0023_customer_settings_text_scale")

    def test_migration_adds_column_with_default_and_preserves_rows(self):
        conn = self._pre_migration_conn()
        try:
            conn.execute(
                "INSERT INTO customer_settings (customer_id, timezone) VALUES (1, 'browser')"
            )
            conn.commit()
            assert run_migrations(conn, [self._migration_0023()]) == 1
            cols = table_columns(conn, "customer_settings")
            assert "text_scale" in cols
            conn.execute("INSERT INTO customer_settings (customer_id) VALUES (2)")
            rows = dict(
                conn.execute(
                    "SELECT customer_id, text_scale FROM customer_settings"
                ).fetchall()
            )
            assert rows[1] == "m"  # existing row gets the default
            assert rows[2] == "m"  # new rows default to 'm' (no visual change)
            tz = conn.execute(
                "SELECT timezone FROM customer_settings WHERE customer_id = 1"
            ).fetchone()
            assert tz == ("browser",)
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        conn = self._pre_migration_conn()
        try:
            run_migrations(conn, [self._migration_0023()])
            assert run_migrations(conn, [self._migration_0023()]) == 0
        finally:
            conn.close()
