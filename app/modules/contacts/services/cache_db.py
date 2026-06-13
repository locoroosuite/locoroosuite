import sqlcipher3

from app.modules.contacts.services.vcard import parse_vcard
from app.shared.cache_errors import CacheKeyMismatchError


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY,
            uid TEXT UNIQUE NOT NULL,
            href TEXT UNIQUE,
            etag TEXT,
            fn TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            email_work TEXT,
            email_home TEXT,
            tel_work TEXT,
            tel_home TEXT,
            tel_cell TEXT,
            org TEXT,
            title TEXT,
            note TEXT,
            raw_vcard TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS addressbook_state (
            href TEXT PRIMARY KEY,
            sync_token TEXT,
            last_sync_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS contacts_fts USING fts5(
            fn, email_work, email_home, tel_work, tel_home, tel_cell,
            content='contacts', content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS contacts_ai AFTER INSERT ON contacts BEGIN
            INSERT INTO contacts_fts(rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES (new.id, new.fn, new.email_work, new.email_home, new.tel_work, new.tel_home, new.tel_cell);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS contacts_ad AFTER DELETE ON contacts BEGIN
            INSERT INTO contacts_fts(contacts_fts, rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES ('delete', old.id, old.fn, old.email_work, old.email_home, old.tel_work, old.tel_home, old.tel_cell);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS contacts_au AFTER UPDATE ON contacts BEGIN
            INSERT INTO contacts_fts(contacts_fts, rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES ('delete', old.id, old.fn, old.email_work, old.email_home, old.tel_work, old.tel_home, old.tel_cell);
            INSERT INTO contacts_fts(rowid, fn, email_work, email_home, tel_work, tel_home, tel_cell)
            VALUES (new.id, new.fn, new.email_work, new.email_home, new.tel_work, new.tel_home, new.tel_cell);
        END
        """
    )
    conn.commit()


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
        if row[2]:
            emails.append({"email": row[2], "type": "work"})
        if row[3]:
            emails.append({"email": row[3], "type": "home"})
        results.append({"uid": row[0], "fn": row[1], "emails": emails})
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
    return {"sync_token": row[0], "last_sync_at": row[1]}


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
