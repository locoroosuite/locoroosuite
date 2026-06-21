"""Unified, dependency-free, versioned database migration runner.

Used by all five database layers in the application:

* The main app database (SQLAlchemy / sqlite3) via ``db.engine.raw_connection()``.
* The per-account encrypted cache databases (mail, docs, contacts, calendar)
  via raw ``sqlcipher3`` connections.

Design — two-layer robustness
=============================

1. **Applied-migrations tracking table** (``_schema_migrations``). Records every
   migration that has run, giving a fast skip-path and an inspectable audit
   trail.

2. **Self-guarding migration functions.** Every migration inspects the actual
   schema and no-ops if the change is already present. This means a database
   created *before* the tracking table existed bootstraps correctly: the runner
   runs the full chain once, each step either no-ops or performs a real fix
   depending on the actual state. No version-stamping heuristics required.

This combination survives fresh databases, old databases, partially-migrated
databases, and corrupt-schema databases (the case that motivated this module:
a stale ``account_id NOT NULL`` column on the mail ``folders`` table).

Adding a new migration
======================

1. Append a ``Migration("NNNN_descriptive_name", fn)`` to the relevant module's
   migration registry (``*_migrations.py``).
2. The function must self-guard: check the current schema and return early if
   the change is already applied.
3. Do **not** call ``conn.commit()`` inside the migration — the runner commits
   after recording the migration. Use ``conn.rollback()`` only if you need to
   recover within the function before re-raising.
4. Add a test that builds the pre-migration schema, runs migrations, and
   asserts the post-migration shape + data preservation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)

# A migration function receives a DBAPI-2 connection (``sqlcipher3``,
# ``sqlite3``, or a SQLAlchemy raw-connection proxy) and performs its schema
# change. It **must** self-guard: check whether the change is already applied
# and return immediately if so.
MigrationFn = Callable[[Any], None]


class Migration:
    """A named, self-guarding schema migration."""

    __slots__ = ("name", "fn")

    def __init__(self, name: str, fn: MigrationFn):
        if not name or not name.strip():
            raise ValueError("migration name must be a non-empty string")
        self.name = name
        self.fn = fn

    def __repr__(self) -> str:
        return f"Migration({self.name!r})"


_MIGRATIONS_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS _schema_migrations ("
    "name TEXT PRIMARY KEY, "
    "applied_at TEXT NOT NULL"
    ")"
)


def ensure_migrations_table(conn: Any) -> None:
    """Create the ``_schema_migrations`` tracking table if it does not exist."""
    conn.execute(_MIGRATIONS_TABLE_DDL)
    conn.commit()


def applied_migrations(conn: Any) -> set[str]:
    """Return the set of migration names recorded as applied."""
    ensure_migrations_table(conn)
    rows = conn.execute("SELECT name FROM _schema_migrations").fetchall()
    return {row[0] for row in rows}


def run_migrations(
    conn: Any,
    migrations: Sequence[Migration],
    *,
    logger: logging.Logger | None = None,
) -> int:
    """Run pending migrations in order.

    Returns the count of newly-applied migrations. Each migration runs in its
    own transaction: the migration function executes, then the runner records
    the migration name in ``_schema_migrations`` and commits. If a migration
    raises, the runner rolls back and re-raises so the caller can handle the
    failure (e.g. cache DBs retry against a fresh file).
    """
    log = logger or _logger
    ensure_migrations_table(conn)
    already = {
        row[0]
        for row in conn.execute("SELECT name FROM _schema_migrations").fetchall()
    }
    applied = 0
    for migration in migrations:
        if migration.name in already:
            continue
        log.debug("applying migration %s", migration.name)
        try:
            migration.fn(conn)
        except Exception:
            conn.rollback()
            log.exception("migration failed: %s", migration.name)
            raise
        conn.execute(
            "INSERT INTO _schema_migrations(name, applied_at) VALUES (?, ?)",
            (migration.name, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# Introspection helpers used by self-guarding migration functions
# ---------------------------------------------------------------------------


def has_table(conn: Any, table: str) -> bool:
    """Return True if ``table`` exists."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchall()
    return len(rows) > 0


def table_columns(conn: Any, table: str) -> set[str]:
    """Return the set of column names for ``table`` (empty if table missing)."""
    if not has_table(conn, table):
        return set()
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def has_index(conn: Any, index_name: str) -> bool:
    """Return True if an index named ``index_name`` exists."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchall()
    return len(rows) > 0
