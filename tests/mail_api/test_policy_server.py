import asyncio
import sqlite3
import tempfile

import pytest

from policy_server import PolicyServer, _check_and_increment


@pytest.fixture()
def db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sending_limits "
        "(email TEXT PRIMARY KEY, max_per_day INTEGER NOT NULL, "
        "sent_today INTEGER DEFAULT 0, last_reset_date TEXT)"
    )
    conn.commit()
    yield conn
    conn.close()
    import os
    os.unlink(tmp.name)


def test_no_limit_returns_dunno(db):
    result = _check_and_increment(db, "nolimit@example.com")
    assert result == "DUNNO"


def test_empty_email_returns_dunno(db):
    result = _check_and_increment(db, "")
    assert result == "DUNNO"


def test_under_limit_returns_dunno(db):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    db.execute(
        "INSERT INTO sending_limits (email, max_per_day, sent_today, last_reset_date) VALUES (?, ?, ?, ?)",
        ("user@example.com", 200, 50, today),
    )
    db.commit()
    result = _check_and_increment(db, "user@example.com")
    assert result == "DUNNO"

    row = db.execute("SELECT sent_today FROM sending_limits WHERE email=?", ("user@example.com",)).fetchone()
    assert row[0] == 51


def test_at_limit_returns_reject(db):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    db.execute(
        "INSERT INTO sending_limits (email, max_per_day, sent_today, last_reset_date) VALUES (?, ?, ?, ?)",
        ("user@example.com", 200, 200, today),
    )
    db.commit()
    result = _check_and_increment(db, "user@example.com")
    assert result.startswith("REJECT")


def test_daily_reset(db):
    db.execute(
        "INSERT INTO sending_limits (email, max_per_day, sent_today, last_reset_date) VALUES (?, ?, ?, ?)",
        ("user@example.com", 200, 200, "2020-01-01"),
    )
    db.commit()
    result = _check_and_increment(db, "user@example.com")
    assert result == "DUNNO"

    row = db.execute("SELECT sent_today FROM sending_limits WHERE email=?", ("user@example.com",)).fetchone()
    assert row[0] == 1


def test_policy_server_start_stop():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    server = PolicyServer(host="127.0.0.1", port=0, db_path=tmp.name)
    asyncio.run(_start_stop(server))
    import os
    os.unlink(tmp.name)


async def _start_stop(server):
    await server.start()
    assert server._server is not None
    assert server._conn is not None
    await server.stop()
