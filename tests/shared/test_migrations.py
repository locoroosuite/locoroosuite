"""Tests for the unified migration runner (``app.shared.migrations``).

Verifies the two-layer robustness model:
1. Applied-migrations tracking table records and skips completed migrations.
2. Self-guarding migration functions are safe to re-run against any state.
"""

import sqlcipher3

from app.shared.migrations import (
    Migration,
    applied_migrations,
    ensure_migrations_table,
    has_index,
    has_table,
    run_migrations,
    table_columns,
)


def _connect(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
    return conn


class TestMigrationRunner:
    def test_creates_tracking_table(self, tmp_path):
        conn = _connect(tmp_path)
        ensure_migrations_table(conn)
        assert has_table(conn, "_schema_migrations")
        cols = table_columns(conn, "_schema_migrations")
        assert cols == {"name", "applied_at"}
        conn.close()

    def test_runs_migrations_in_order_and_records(self, tmp_path):
        conn = _connect(tmp_path)
        calls = []

        def make_fn(label):
            def fn(c):
                calls.append(label)
            return fn

        migrations = (
            Migration("0001_first", make_fn("first")),
            Migration("0002_second", make_fn("second")),
            Migration("0003_third", make_fn("third")),
        )

        applied = run_migrations(conn, migrations)
        assert applied == 3
        assert calls == ["first", "second", "third"]
        assert applied_migrations(conn) == {"0001_first", "0002_second", "0003_third"}
        conn.close()

    def test_skips_already_applied(self, tmp_path):
        conn = _connect(tmp_path)
        calls = []

        def fn_a(c):
            calls.append("a")

        def fn_b(c):
            calls.append("b")

        run_migrations(conn, (Migration("0001_a", fn_a),))
        assert calls == ["a"]

        applied = run_migrations(conn, (Migration("0001_a", fn_a), Migration("0002_b", fn_b)))
        assert applied == 1
        assert calls == ["a", "b"]
        conn.close()

    def test_self_guarding_migration_is_idempotent(self, tmp_path):
        conn = _connect(tmp_path)

        def add_col(c):
            if "extra" not in table_columns(c, "items"):
                c.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, val TEXT)")
            if "extra" not in table_columns(c, "items"):
                c.execute("ALTER TABLE items ADD COLUMN extra TEXT")

        run_migrations(conn, (Migration("0001_add_col", add_col),))
        # Running the same migration function directly again is safe
        add_col(conn)
        assert "extra" in table_columns(conn, "items")
        conn.close()

    def test_re_records_on_second_run_set(self, tmp_path):
        """A migration not in the tracking table runs even if the schema
        change was already present (bootstrap path for pre-versioning DBs)."""
        conn = _connect(tmp_path)
        conn.execute("CREATE TABLE widgets(id INTEGER PRIMARY KEY)")
        conn.commit()

        def add_name(c):
            if "name" not in table_columns(c, "widgets"):
                c.execute("ALTER TABLE widgets ADD COLUMN name TEXT")

        run_migrations(conn, (Migration("0001_add_name", add_name),))
        assert "name" in table_columns(conn, "widgets")
        assert "0001_add_name" in applied_migrations(conn)
        conn.close()

    def test_rolls_back_on_failure(self, tmp_path):
        conn = _connect(tmp_path)
        conn.execute("CREATE TABLE data(id INTEGER PRIMARY KEY)")
        conn.commit()

        def good(c):
            c.execute("ALTER TABLE data ADD COLUMN a TEXT")

        def bad(c):
            raise RuntimeError("boom")

        try:
            run_migrations(conn, (Migration("0001_good", good), Migration("0002_bad", bad)))
            assert False, "should have raised"
        except RuntimeError as exc:
            assert "boom" in str(exc)

        assert "0001_good" in applied_migrations(conn)
        assert "0002_bad" not in applied_migrations(conn)
        conn.close()

    def test_empty_migration_list(self, tmp_path):
        conn = _connect(tmp_path)
        assert run_migrations(conn, ()) == 0
        assert has_table(conn, "_schema_migrations")
        conn.close()


class TestIntrospectionHelpers:
    def test_has_table(self, tmp_path):
        conn = _connect(tmp_path)
        assert not has_table(conn, "nope")
        conn.execute("CREATE TABLE real(id INTEGER)")
        conn.commit()
        assert has_table(conn, "real")
        conn.close()

    def test_table_columns_missing_table(self, tmp_path):
        conn = _connect(tmp_path)
        assert table_columns(conn, "missing") == set()
        conn.close()

    def test_has_index(self, tmp_path):
        conn = _connect(tmp_path)
        conn.execute("CREATE TABLE t(id INTEGER, val INTEGER)")
        conn.execute("CREATE INDEX t_val_idx ON t(val)")
        conn.commit()
        assert has_index(conn, "t_val_idx")
        assert not has_index(conn, "nonexistent_idx")
        conn.close()


class TestMigrationNameValidation:
    def test_empty_name_rejected(self):
        try:
            Migration("", lambda c: None)
            assert False
        except ValueError:
            pass

    def test_valid_name(self):
        m = Migration("0001_test", lambda c: None)
        assert m.name == "0001_test"
