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
