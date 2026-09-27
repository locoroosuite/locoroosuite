"""Push dedup log for headless chat notifications (HLD U25.61).

One row per pushed event id in the per-user encrypted chat cache, so a
worker crash between "push sent" and "sync token committed" can never
re-ring the same event, and replays across restarts are idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

RETENTION = timedelta(days=30)


def was_pushed(conn, event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM chat_push_log WHERE event_id = ? LIMIT 1", (event_id,)
    ).fetchone()
    return row is not None


def mark_pushed(conn, event_id: str, kind: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO chat_push_log (event_id, kind, pushed_at) VALUES (?, ?, ?)",
        (event_id, kind, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def prune_push_log(conn, now: datetime | None = None) -> None:
    cutoff = (now or datetime.now(UTC)) - RETENTION
    conn.execute("DELETE FROM chat_push_log WHERE pushed_at < ?", (cutoff.isoformat(),))
    conn.commit()
