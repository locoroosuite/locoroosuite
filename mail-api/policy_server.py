from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SENDING_LIMITS_DB = os.environ.get("SENDING_LIMITS_DB", "/var/lib/mail-api/sending_limits.db")

REJECT_MESSAGE = "554 5.7.1 Daily sending limit reached. Limit resets at midnight UTC."
DEFAULT_ACTION = "DUNNO"


def _init_db(db_path: str) -> sqlite3.Connection:
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sending_limits "
        "(email TEXT PRIMARY KEY, max_per_day INTEGER NOT NULL, "
        "sent_today INTEGER DEFAULT 0, last_reset_date TEXT)"
    )
    conn.commit()
    return conn


def _check_and_increment(conn: sqlite3.Connection, email: str) -> str:
    if not email:
        return DEFAULT_ACTION

    row = conn.execute(
        "SELECT max_per_day, sent_today, last_reset_date FROM sending_limits WHERE email=?",
        (email,),
    ).fetchone()

    if not row:
        return DEFAULT_ACTION

    max_per_day, sent_today, last_reset_date = row
    today = datetime.now(timezone.utc).date().isoformat()

    if last_reset_date != today:
        sent_today = 0
        last_reset_date = today

    if sent_today >= max_per_day:
        conn.execute(
            "UPDATE sending_limits SET sent_today=?, last_reset_date=? WHERE email=?",
            (sent_today, last_reset_date, email),
        )
        conn.commit()
        return f"REJECT {REJECT_MESSAGE}"

    conn.execute(
        "UPDATE sending_limits SET sent_today=?, last_reset_date=? WHERE email=?",
        (sent_today + 1, last_reset_date, email),
    )
    conn.commit()
    return DEFAULT_ACTION


async def _handle_policy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, conn: sqlite3.Connection) -> None:
    try:
        attributes: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded == "":
                break
            if "=" in decoded:
                key, _, value = decoded.partition("=")
                attributes[key.strip()] = value.strip()

        sasl_username = attributes.get("sasl_username", "")
        action = _check_and_increment(conn, sasl_username)

        response = f"action={action}\n\n"
        writer.write(response.encode("utf-8"))
        await writer.drain()
    except Exception:
        logger.exception("policy server error handling request")
        try:
            writer.write(f"action={DEFAULT_ACTION}\n\n".encode("utf-8"))
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()


class PolicyServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9900, db_path: str = ""):
        self.host = host
        self.port = port
        self.db_path = db_path or SENDING_LIMITS_DB
        self._server: asyncio.AbstractServer | None = None
        self._conn: sqlite3.Connection | None = None

    async def _client_handler(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_policy(reader, writer, self._conn)

    async def start(self) -> None:
        self._conn = _init_db(self.db_path)
        self._server = await asyncio.start_server(
            self._client_handler, self.host, self.port,
        )
        logger.info("policy server listening on %s:%d", self.host, self.port)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._conn:
            self._conn.close()


def run_server(host: str = "0.0.0.0", port: int = 9900, db_path: str = "") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    server = PolicyServer(host=host, port=port, db_path=db_path)
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_server()
