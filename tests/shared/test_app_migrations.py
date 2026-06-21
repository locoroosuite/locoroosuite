"""Tests for the main application database migration system.

Verifies that the app factory runs the unified migration runner against the
main DB and that all migrations are recorded in ``_schema_migrations``.
"""

from app.shared.app_migrations import APP_DB_MIGRATIONS
from app.shared.db import db
from app.shared.migrations import table_columns


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
