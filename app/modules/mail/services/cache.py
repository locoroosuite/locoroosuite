from pathlib import Path

import sqlcipher3

from app.config import DATA_DIR

CACHE_DIR = DATA_DIR / "caches"
CACHE_DIR.mkdir(exist_ok=True)


def build_cache_path(customer_id, account_id):
    filename = f"customer_{customer_id}_account_{account_id}.db"
    return str(CACHE_DIR / filename)


def purge_cache(path, key=None):
    if not path:
        return
    cache_path = Path(path)
    if not cache_path.exists():
        return
    if key:
        _drop_mail_tables(path, key)
    else:
        cache_path.unlink()


def _drop_mail_tables(path, key):
    conn = sqlcipher3.connect(path)
    try:
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        conn.execute("DELETE FROM message_tags")
        conn.execute("DELETE FROM tags")
        conn.execute("DELETE FROM folder_state")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM folders")
        conn.commit()
    finally:
        conn.close()
