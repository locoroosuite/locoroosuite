import sqlcipher3

from app.modules.contacts.services.cache_migrations import CONTACTS_CACHE_MIGRATIONS
from app.modules.contacts.services.vcard import parse_vcard
from app.shared.cache_errors import CacheKeyMismatchError
from app.shared.migrations import run_migrations


def open_cache(db_path, key):
    if not key:
        raise ValueError("cache key required")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    try:
        _init_schema(conn)
    except (MemoryError, Exception) as exc:
        conn.close()
        import os as _os
        if _os.path.exists(db_path):
            _os.unlink(db_path)
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        try:
            _init_schema(conn)
        except Exception:
            conn.close()
            raise CacheKeyMismatchError(
                f"Failed to open cache database even after reset. db_path={db_path}"
            ) from exc
    return conn


def _init_schema(conn):
    run_migrations(conn, CONTACTS_CACHE_MIGRATIONS)


def upsert_contact(conn, uid, href, etag, vcard_text):
    parsed = parse_vcard(vcard_text)
    row = conn.execute("SELECT id FROM contacts WHERE uid = ?", (uid,)).fetchone()
    fields = {
        "uid": uid,
        "href": href,
        "etag": etag,
        "fn": parsed.get("fn") or "",
        "last_name": parsed.get("last_name") or "",
        "first_name": parsed.get("first_name") or "",
        "email_work": parsed.get("email_work"),
        "email_home": parsed.get("email_home"),
        "tel_work": parsed.get("tel_work"),
        "tel_home": parsed.get("tel_home"),
        "tel_cell": parsed.get("tel_cell"),
        "org": parsed.get("org"),
        "title": parsed.get("title"),
        "note": parsed.get("note"),
        "raw_vcard": vcard_text,
        "updated_at": _now(),
    }
    if row:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE contacts SET {sets} WHERE id = ?", (*fields.values(), row[0]))
    else:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO contacts ({cols}) VALUES ({placeholders})", tuple(fields.values()))
    conn.commit()
    return conn.execute("SELECT id FROM contacts WHERE uid = ?", (uid,)).fetchone()[0]


def delete_contact_by_uid(conn, uid):
    conn.execute("DELETE FROM contacts WHERE uid = ?", (uid,))
    conn.commit()


def get_contact(conn, contact_id):
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM contacts LIMIT 0").description]
    return dict(zip(cols, row))


def get_contact_by_uid(conn, uid):
    row = conn.execute("SELECT * FROM contacts WHERE uid = ?", (uid,)).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM contacts LIMIT 0").description]
    return dict(zip(cols, row))


def list_contacts(conn, page=1, per_page=50):
    offset = (page - 1) * per_page
    rows = conn.execute(
        "SELECT * FROM contacts ORDER BY fn COLLATE NOCASE ASC LIMIT ? OFFSET ?",
        (per_page, offset),
    ).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM contacts LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def count_contacts(conn):
    row = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()
    return row[0] if row else 0


def search_contacts(conn, query, page=1, per_page=50):
    offset = (page - 1) * per_page
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT * FROM contacts
        WHERE fn LIKE ? OR email_work LIKE ? OR email_home LIKE ?
           OR tel_work LIKE ? OR tel_home LIKE ? OR tel_cell LIKE ?
           OR org LIKE ? OR title LIKE ?
        ORDER BY fn COLLATE NOCASE ASC
        LIMIT ? OFFSET ?
        """,
        (like, like, like, like, like, like, like, like, per_page, offset),
    ).fetchall()
    cols = [desc[0] for desc in conn.execute("SELECT * FROM contacts LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def search_contacts_api(conn, query, limit=10):
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT uid, fn, email_work, email_home
        FROM contacts
        WHERE fn LIKE ? OR email_work LIKE ? OR email_home LIKE ?
        ORDER BY fn COLLATE NOCASE ASC
        LIMIT ?
        """,
        (like, like, like, limit),
    ).fetchall()
    results = []
    for row in rows:
        emails = []
        if row["email_work"]:
            emails.append({"email": row["email_work"], "type": "work"})
        if row["email_home"]:
            emails.append({"email": row["email_home"], "type": "home"})
        results.append({"uid": row["uid"], "fn": row["fn"], "emails": emails})
    return results


def email_exists(conn, email):
    row = conn.execute(
        "SELECT 1 FROM contacts WHERE email_work = ? OR email_home = ? LIMIT 1",
        (email, email),
    ).fetchone()
    return row is not None


def find_by_email(conn, email):
    row = conn.execute(
        "SELECT * FROM contacts WHERE email_work = ? OR email_home = ? LIMIT 1",
        (email, email),
    ).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM contacts LIMIT 0").description]
    return dict(zip(cols, row))


def get_sync_state(conn, href):
    row = conn.execute(
        "SELECT sync_token, last_sync_at FROM addressbook_state WHERE href = ?",
        (href,),
    ).fetchone()
    if not row:
        return None
    return {"sync_token": row["sync_token"], "last_sync_at": row["last_sync_at"]}


def set_sync_state(conn, href, sync_token=None):
    conn.execute(
        """
        INSERT INTO addressbook_state (href, sync_token, last_sync_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(href) DO UPDATE SET sync_token = excluded.sync_token, last_sync_at = datetime('now')
        """,
        (href, sync_token),
    )
    conn.commit()


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
