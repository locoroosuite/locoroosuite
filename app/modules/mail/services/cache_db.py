from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re

import sqlcipher3

from app.modules.mail.services.folder_sort import UNREAD_EXCLUDED_FOLDERS
from app.shared.cache_errors import CacheKeyMismatchError


def open_cache(db_path, key):
    if not key:
        raise ValueError("cache key required")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    try:
        init_cache_schema(conn)
    except (MemoryError, Exception) as exc:
        conn.close()
        import os as _os
        if _os.path.exists(db_path):
            _os.unlink(db_path)
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        try:
            init_cache_schema(conn)
        except Exception:
            conn.close()
            raise CacheKeyMismatchError(
                f"Failed to open cache database even after reset. db_path={db_path}"
            ) from exc
    return conn


def _date_to_unix(date_value):
    if not date_value:
        return None
    if isinstance(date_value, datetime):
        dt = date_value
    else:
        try:
            dt = parsedate_to_datetime(date_value)
        except (TypeError, ValueError, IndexError):
            return None
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _ensure_date_ts_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "date_ts" in columns:
        return
    conn.execute("ALTER TABLE messages ADD COLUMN date_ts INTEGER")
    rows = conn.execute("SELECT id, date FROM messages").fetchall()
    for message_id, date_value in rows:
        conn.execute(
            "UPDATE messages SET date_ts = ? WHERE id = ?",
            (_date_to_unix(date_value), message_id),
        )
    conn.commit()


def _ensure_internal_date_ts_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "internal_date_ts" in columns:
        return
    conn.execute("ALTER TABLE messages ADD COLUMN internal_date_ts INTEGER")
    conn.commit()


def _ensure_thread_columns(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    altered = False
    if "in_reply_to" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN in_reply_to TEXT")
        altered = True
    if "ref_list" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN ref_list TEXT")
        altered = True
    if "thread_id" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN thread_id TEXT")
        altered = True
    if altered:
        conn.commit()


def parse_references_header(value):
    if not value:
        return []
    return re.findall(r'<([^>]+)>', str(value))


def compute_thread_id(message_id, in_reply_to, ref_list):
    refs = parse_references_header(ref_list)
    if refs:
        return refs[0]
    irt_refs = parse_references_header(in_reply_to)
    if irt_refs:
        return irt_refs[0]
    if message_id:
        mid = str(message_id).strip()
        if mid.startswith('<') and mid.endswith('>'):
            mid = mid[1:-1]
        return mid or None
    return None


def _ensure_calendar_link_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "calendar_event_uid" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN calendar_event_uid TEXT")
        conn.commit()


def _ensure_bounce_columns(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    altered = False
    if "is_bounce" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN is_bounce INTEGER DEFAULT 0")
        altered = True
    if "bounce_reason" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN bounce_reason TEXT")
        altered = True
    if "original_subject" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN original_subject TEXT")
        altered = True
    if altered:
        conn.commit()


def _ensure_cc_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "cc" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN cc TEXT")
        conn.commit()


def _ensure_attachment_list_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "attachment_list" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN attachment_list TEXT")
        conn.commit()


def _ensure_body_html_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "body_html" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN body_html TEXT")
        conn.commit()


def init_cache_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            unread_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS folder_state (
            folder TEXT PRIMARY KEY,
            uidvalidity TEXT,
            uidnext INTEGER,
            highestmodseq TEXT,
            last_sync_at TEXT,
            last_new_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            recipients TEXT,
            date TEXT,
            date_ts INTEGER,
            flags TEXT,
            snippet TEXT,
            body TEXT,
            has_attachments INTEGER DEFAULT 0,
            message_id TEXT,
            thread_id TEXT
        )
        """
    )
    _ensure_date_ts_column(conn)
    _ensure_internal_date_ts_column(conn)
    _ensure_thread_columns(conn)
    _ensure_calendar_link_column(conn)
    _ensure_bounce_columns(conn)
    _ensure_cc_column(conn)
    _ensure_attachment_list_column(conn)
    _ensure_body_html_column(conn)
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS messages_uid_folder_idx ON messages(uid, folder)")
    except sqlcipher3.dbapi2.IntegrityError:
        conn.execute(
            """
            DELETE FROM messages
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM messages
                WHERE uid IS NOT NULL AND folder IS NOT NULL
                GROUP BY uid, folder
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS messages_uid_folder_idx ON messages(uid, folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS messages_folder_date_ts_idx ON messages(folder, COALESCE(internal_date_ts, date_ts) DESC, id DESC)")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
            subject, sender, recipients, body, content='messages', content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_tags (
            message_id INTEGER,
            tag_id INTEGER,
            PRIMARY KEY (message_id, tag_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO message_fts(rowid, subject, sender, recipients, body)
            VALUES (new.id, new.subject, new.sender, new.recipients, new.body);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO message_fts(message_fts, rowid, subject, sender, recipients, body)
            VALUES ('delete', old.id, old.subject, old.sender, old.recipients, old.body);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO message_fts(message_fts, rowid, subject, sender, recipients, body)
            VALUES ('delete', old.id, old.subject, old.sender, old.recipients, old.body);
            INSERT INTO message_fts(rowid, subject, sender, recipients, body)
            VALUES (new.id, new.subject, new.sender, new.recipients, new.body);
        END;
        """
    )
    conn.commit()


def upsert_folder(conn, name, unread_count=0):
    conn.execute(
        "INSERT INTO folders(name, unread_count) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET unread_count=excluded.unread_count",
        (name, unread_count),
    )
    conn.commit()


def list_cached_folders(conn):
    cursor = conn.execute("SELECT name, unread_count FROM folders ORDER BY name ASC")
    return cursor.fetchall()


def get_folder_state(conn, folder):
    cursor = conn.execute(
        """
        SELECT folder, uidvalidity, uidnext, highestmodseq, last_sync_at, last_new_at
        FROM folder_state
        WHERE folder = ?
        """,
        (folder,),
    )
    return cursor.fetchone()


def upsert_folder_state(conn, folder, uidvalidity=None, uidnext=None, highestmodseq=None, last_sync_at=None, last_new_at=None):
    conn.execute(
        """
        INSERT INTO folder_state(folder, uidvalidity, uidnext, highestmodseq, last_sync_at, last_new_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(folder) DO UPDATE SET
            uidvalidity=excluded.uidvalidity,
            uidnext=excluded.uidnext,
            highestmodseq=excluded.highestmodseq,
            last_sync_at=excluded.last_sync_at,
            last_new_at=COALESCE(excluded.last_new_at, folder_state.last_new_at)
        """,
        (folder, uidvalidity, uidnext, highestmodseq, last_sync_at, last_new_at),
    )
    conn.commit()


def update_last_new_at(conn, folder, last_new_at):
    conn.execute(
        "UPDATE folder_state SET last_new_at = ? WHERE folder = ?",
        (last_new_at, folder),
    )
    conn.commit()


def list_recent_active_folders(conn, since_iso):
    cursor = conn.execute(
        """
        SELECT folder
        FROM folder_state
        WHERE last_new_at IS NOT NULL AND last_new_at >= ?
        ORDER BY last_new_at DESC
        """,
        (since_iso,),
    )
    rows = cursor.fetchall()
    return [row[0] for row in rows]


def has_completed_sync(conn):
    cursor = conn.execute(
        "SELECT 1 FROM folder_state WHERE last_sync_at IS NOT NULL LIMIT 1"
    )
    return cursor.fetchone() is not None


def upsert_message(
    conn,
    uid,
    folder,
    subject,
    sender,
    recipients,
    date,
    flags,
    snippet,
    body,
    has_attachments,
    message_id,
    thread_id=None,
    date_ts=None,
    in_reply_to=None,
    ref_list=None,
    internal_date=None,
    is_bounce=False,
    bounce_reason=None,
    original_subject=None,
    cc=None,
    attachment_list=None,
    body_html=None,
):
    date_ts_value = _date_to_unix(date) if date_ts is None else date_ts
    internal_date_ts_value = _date_to_unix(internal_date) if internal_date else None
    if thread_id is None:
        thread_id = compute_thread_id(message_id, in_reply_to, ref_list)
    conn.execute(
        """
        INSERT INTO messages(uid, folder, subject, sender, recipients, date, date_ts, internal_date_ts, flags, snippet, body, has_attachments, message_id, thread_id, in_reply_to, ref_list, is_bounce, bounce_reason, original_subject, cc, attachment_list, body_html)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(uid, folder) DO UPDATE SET
            subject=excluded.subject,
            sender=excluded.sender,
            recipients=excluded.recipients,
            date=excluded.date,
            date_ts=excluded.date_ts,
            internal_date_ts=excluded.internal_date_ts,
            flags=excluded.flags,
            snippet=excluded.snippet,
            body=excluded.body,
            has_attachments=excluded.has_attachments,
            message_id=excluded.message_id,
            thread_id=excluded.thread_id,
            in_reply_to=excluded.in_reply_to,
            ref_list=excluded.ref_list,
            is_bounce=excluded.is_bounce,
            bounce_reason=excluded.bounce_reason,
            original_subject=excluded.original_subject,
            cc=excluded.cc,
            attachment_list=excluded.attachment_list,
            body_html=excluded.body_html
        """,
        (
            uid,
            folder,
            subject,
            sender,
            recipients,
            date,
            date_ts_value,
            internal_date_ts_value,
            json.dumps(flags or []),
            snippet,
            body,
            1 if has_attachments else 0,
            message_id,
            thread_id,
            in_reply_to,
            ref_list,
            1 if is_bounce else 0,
            bounce_reason,
            original_subject,
            cc,
            json.dumps(attachment_list) if attachment_list else None,
            body_html,
        ),
    )
    conn.commit()


def list_message_uids(conn, folder):
    cursor = conn.execute("SELECT uid FROM messages WHERE folder = ? ORDER BY uid ASC", (folder,))
    return [row[0] for row in cursor.fetchall()]


def list_uids_missing_internal_date_ts(conn, folder):
    cursor = conn.execute(
        "SELECT uid FROM messages WHERE folder = ? AND internal_date_ts IS NULL ORDER BY uid ASC",
        (folder,),
    )
    return [row[0] for row in cursor.fetchall()]


def update_internal_date_ts_for_uid(conn, folder, uid, internal_date_ts):
    conn.execute(
        "UPDATE messages SET internal_date_ts = ? WHERE folder = ? AND uid = ?",
        (internal_date_ts, folder, uid),
    )
    conn.commit()


def delete_message_by_id(conn, message_id):
    conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    conn.commit()


def delete_messages_by_uids(conn, folder, uids):
    if not uids:
        return
    placeholders = ",".join(["?"] * len(uids))
    conn.execute(
        f"DELETE FROM messages WHERE folder = ? AND uid IN ({placeholders})",
        (folder, *uids),
    )
    conn.commit()


def delete_messages_by_folder(conn, folder):
    conn.execute("DELETE FROM messages WHERE folder = ?", (folder,))
    conn.commit()


def rename_folder_in_cache(conn, old_name, new_name):
    conn.execute("UPDATE OR ABORT folders SET name = ? WHERE name = ?", (new_name, old_name))
    conn.execute("UPDATE messages SET folder = ? WHERE folder = ?", (new_name, old_name))
    conn.execute("UPDATE folder_state SET folder = ? WHERE folder = ?", (new_name, old_name))
    conn.commit()


def delete_folder_in_cache(conn, folder):
    conn.execute("DELETE FROM folders WHERE name = ?", (folder,))
    conn.execute("DELETE FROM messages WHERE folder = ?", (folder,))
    conn.execute("DELETE FROM folder_state WHERE folder = ?", (folder,))
    conn.commit()


def delete_folder_state(conn, folder):
    conn.execute("DELETE FROM folder_state WHERE folder = ?", (folder,))
    conn.commit()


def update_flags_for_uid(conn, folder, uid, flags):
    conn.execute(
        "UPDATE messages SET flags = ? WHERE folder = ? AND uid = ?",
        (json.dumps(flags or []), folder, uid),
    )
    conn.commit()


def update_flags_bulk(conn, folder, uid_flags):
    for uid, flags in uid_flags.items():
        conn.execute(
            "UPDATE messages SET flags = ? WHERE folder = ? AND uid = ?",
            (json.dumps(flags or []), folder, uid),
        )
    conn.commit()


def _normalize_fts_query(query):
    if not query:
        return ""
    terms = []
    for token in re.findall(r"\S+", query):
        if not re.search(r"[A-Za-z0-9]", token):
            continue
        safe = token.replace('"', '""')
        terms.append(f'"{safe}"')
    return " AND ".join(terms)


def search_local(conn, query, limit=50):
    normalized = _normalize_fts_query(query or "")
    if not normalized:
        return []
    cursor = conn.execute(
        """
        SELECT messages.id, messages.uid, messages.folder, messages.subject,
               messages.sender, messages.recipients, messages.date, messages.flags,
               messages.body, messages.has_attachments, messages.message_id,
               messages.thread_id, messages.snippet
        FROM message_fts
        JOIN messages ON messages.id = message_fts.rowid
        WHERE message_fts MATCH ?
        ORDER BY COALESCE(messages.internal_date_ts, messages.date_ts, 0) DESC, messages.id DESC
        LIMIT ?
        """,
        (normalized, limit),
    )
    return cursor.fetchall()


def list_messages(conn, folder, limit=100):
    cursor = conn.execute(
        """
        SELECT id, subject, sender, snippet, date, flags, body
        FROM messages
        WHERE folder = ?
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        LIMIT ?
        """,
        (folder, limit),
    )
    return cursor.fetchall()


def list_unread(conn, limit=100):
    excluded = tuple(UNREAD_EXCLUDED_FOLDERS)
    placeholders = ",".join("?" for _ in excluded)
    cursor = conn.execute(
        f"""
        SELECT id, subject, sender, snippet, date, flags, body, folder, thread_id, recipients,
               COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason,
               original_subject, has_attachments
        FROM messages
        WHERE flags NOT LIKE '%Seen%'
          AND UPPER(folder) NOT IN ({placeholders})
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        LIMIT ?
        """,
        (*excluded, limit),
    )
    return cursor.fetchall()


def list_flagged(conn, limit=100):
    cursor = conn.execute(
        """
        SELECT id, subject, sender, snippet, date, flags, body, folder, thread_id, recipients,
               COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason,
               original_subject, has_attachments
        FROM messages
        WHERE flags IS NOT NULL AND flags != ''
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        LIMIT ?
        """,
        (limit * 5,),
    )
    rows = cursor.fetchall()
    flagged = []
    for row in rows:
        try:
            flags = json.loads(row["flags"]) if row["flags"] else []
        except (TypeError, ValueError):
            flags = []
        if "\\Flagged" in flags:
            flagged.append(row)
        if len(flagged) >= limit:
            break
    return flagged


def list_with_attachments(conn, limit=100):
    cursor = conn.execute(
        """
        SELECT id, subject, sender, snippet, date, flags, body, folder, thread_id, recipients,
               COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason,
               original_subject, has_attachments
        FROM messages
        WHERE has_attachments = 1
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()


def count_unread_flagged(conn):
    excluded = tuple(UNREAD_EXCLUDED_FOLDERS)
    placeholders = ",".join("?" for _ in excluded)
    cursor = conn.execute(
        f"""
        SELECT flags
        FROM messages
        WHERE flags IS NOT NULL AND flags != ''
          AND UPPER(folder) NOT IN ({placeholders})
        """,
        excluded,
    )
    rows = cursor.fetchall()
    total = 0
    for row in rows:
        try:
            flags = json.loads(row[0]) if row[0] else []
        except (TypeError, ValueError):
            flags = []
        if "\\Flagged" in flags and "\\Seen" not in flags:
            total += 1
    return total


def get_message(conn, message_id):
    cursor = conn.execute(
        """
        SELECT id, uid, folder, subject, sender, recipients, date, flags, snippet, body, body_html, has_attachments, message_id, thread_id, cc
        FROM messages
        WHERE id = ?
        """,
        (message_id,),
    )
    return cursor.fetchone()


def get_message_by_uid_and_folder(conn, uid, folder):
    cursor = conn.execute(
        """
        SELECT id, uid, folder, subject, sender, recipients, date, flags, snippet, body, body_html, has_attachments, message_id, thread_id, cc
        FROM messages
        WHERE uid = ? AND folder = ?
        """,
        (str(uid), folder),
    )
    return cursor.fetchone()


def list_thread_messages(conn, thread_id):
    cursor = conn.execute(
        """
        SELECT id, uid, folder, subject, sender, recipients, date, flags, snippet, body, body_html, has_attachments, message_id, thread_id, COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject, cc, attachment_list
        FROM messages
        WHERE thread_id = ?
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) ASC, id ASC
        """,
        (thread_id,),
    )
    return cursor.fetchall()


def update_flags(conn, message_id, flags):
    conn.execute(
        "UPDATE messages SET flags = ? WHERE id = ?",
        (json.dumps(flags or []), message_id),
    )
    conn.commit()


def create_tag(conn, name):
    conn.execute("INSERT INTO tags(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
    conn.commit()


def list_tags(conn):
    cursor = conn.execute("SELECT id, name FROM tags ORDER BY name ASC")
    return cursor.fetchall()


def tag_message(conn, message_id, tag_id):
    conn.execute("INSERT OR IGNORE INTO message_tags(message_id, tag_id) VALUES (?, ?)", (message_id, tag_id))
    conn.commit()


def list_messages_by_tag(conn, tag_id, limit=100):
    cursor = conn.execute(
        """
        SELECT messages.id, messages.subject, messages.sender, messages.snippet, messages.date,
               messages.flags, messages.body, messages.folder, messages.thread_id, messages.recipients,
               COALESCE(messages.internal_date_ts, messages.date_ts) AS sort_ts,
               messages.is_bounce, messages.bounce_reason, messages.original_subject, messages.has_attachments
        FROM message_tags
        JOIN messages ON messages.id = message_tags.message_id
        WHERE message_tags.tag_id = ?
        ORDER BY COALESCE(messages.internal_date_ts, messages.date_ts, 0) DESC, messages.id DESC
        LIMIT ?
        """,
        (tag_id, limit),
    )
    return cursor.fetchall()


def count_messages_in_folder(conn, folder):
    cursor = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE folder = ?",
        (folder,),
    )
    return cursor.fetchone()[0]


def list_messages_for_folder_view(conn, folder):
    cursor = conn.execute(
        """
        SELECT id, subject, sender, snippet, date, flags, NULL AS body, folder, thread_id, recipients,
               COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject,
               has_attachments
        FROM messages
        WHERE folder = ?
        ORDER BY CASE WHEN COALESCE(internal_date_ts, date_ts, 0) = 0 THEN 0 ELSE 1 END,
                 COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        """,
        (folder,),
    )
    return cursor.fetchall()


def list_sent_for_threading(conn):
    cursor = conn.execute(
        """
        SELECT id, subject, sender, snippet, date, flags, NULL AS body, folder, thread_id, recipients,
               COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject,
               has_attachments
        FROM messages
        WHERE LOWER(folder) IN ('sent', 'sent items', 'sent messages')
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        """,
    )
    return cursor.fetchall()


def list_drafts_for_threading(conn):
    cursor = conn.execute(
        """
        SELECT id, subject, sender, snippet, date, flags, NULL AS body, folder, thread_id, recipients,
               COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject,
               has_attachments
        FROM messages
        WHERE LOWER(folder) = 'drafts'
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        """,
    )
    return cursor.fetchall()


def list_messages_with_threading(conn, folder, limit=100, after_id=None):
    if after_id:
        cursor = conn.execute(
            """
            SELECT id, subject, sender, snippet, date, flags, body, folder, thread_id, recipients, COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject
            FROM messages
            WHERE folder = ? AND id < ?
            ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
            LIMIT ?
            """,
            (folder, after_id, limit),
        )
    else:
        cursor = conn.execute(
            """
            SELECT id, subject, sender, snippet, date, flags, body, folder, thread_id, recipients, COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject
            FROM messages
            WHERE folder = ?
            ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
            LIMIT ?
            """,
            (folder, limit),
        )
    return cursor.fetchall()


def list_sent_messages(conn, limit=200):
    cursor = conn.execute(
        """
        SELECT id, subject, sender, snippet, date, flags, body, folder, thread_id, recipients, COALESCE(internal_date_ts, date_ts) AS sort_ts, is_bounce, bounce_reason, original_subject
        FROM messages
        WHERE LOWER(folder) IN ('sent', 'sent items', 'sent messages')
        ORDER BY COALESCE(internal_date_ts, date_ts, 0) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()
