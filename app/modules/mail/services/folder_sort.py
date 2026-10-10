from datetime import UTC
from email.utils import parsedate_to_datetime

from app.modules.mail.services.folder_aliases import FOLDER_ALIASES, canonical_folder_key

SYSTEM_FOLDERS: list[str] = list(FOLDER_ALIASES.keys())

UNREAD_EXCLUDED_FOLDERS = {"TRASH", "JUNK", "DRAFTS"}


def build_folder_sections(folders, pinned, conn):
    candidates_by_key = {}
    keys_in_order = []
    for folder in folders:
        key = canonical_folder_key(folder)
        if key not in candidates_by_key:
            candidates_by_key[key] = []
            keys_in_order.append(key)
        if folder not in candidates_by_key[key]:
            candidates_by_key[key].append(folder)

    actual_by_key = {}
    for key, candidates in candidates_by_key.items():
        if len(candidates) == 1:
            actual_by_key[key] = candidates[0]
        elif key == "inbox":
            actual_by_key[key] = next((f for f in candidates if f == "INBOX"), candidates[0])
        else:
            actual_by_key[key] = _representative_folder(conn, candidates)

    inbox_name = actual_by_key.get("inbox")
    system_keys = set(FOLDER_ALIASES.keys())

    pinned_keys = []
    for name in pinned or []:
        key = canonical_folder_key(name)
        if key in actual_by_key and key not in pinned_keys:
            pinned_keys.append(key)

    pinned_keys = [key for key in pinned_keys if key != "inbox" and key not in system_keys]
    pinned_folders = [actual_by_key[key] for key in pinned_keys]

    system_folders = []
    for sys_key in SYSTEM_FOLDERS:
        if sys_key in actual_by_key:
            system_folders.append(actual_by_key[sys_key])

    used_keys = set(pinned_keys) | system_keys
    if inbox_name:
        used_keys.add("inbox")

    remaining = [actual_by_key[key] for key in keys_in_order if key not in used_keys]
    latest_dates = _latest_message_dates(conn, remaining)
    with_dates = [folder for folder in remaining if folder in latest_dates]
    no_dates = [folder for folder in remaining if folder not in latest_dates]
    with_dates.sort(key=lambda name: latest_dates[name], reverse=True)
    recent_folders = with_dates + no_dates

    sections = []
    if inbox_name:
        sections.append({"title": "INBOX", "folders": [inbox_name]})
    if pinned_folders:
        sections.append({"title": "Favorites", "folders": pinned_folders})
    if system_folders:
        sections.append({"title": "System", "folders": system_folders})
    # U4.15/U4.15a: the Folders section (and its "+" create affordance in
    # folder.html) must render even when empty, otherwise accounts without
    # custom folders can never create their first one.
    sections.append({"title": "Folders", "folders": recent_folders})
    return sections


def _representative_folder(conn, candidates):
    """U5.4a: when several server mailboxes share a canonical key (e.g. Sent
    and Sent Messages), the sidebar shows the one holding cached messages —
    latest activity wins; falls back to server LIST order when none has any."""
    dates = _latest_message_dates(conn, candidates)
    dated = [f for f in candidates if f in dates]
    if not dated:
        return candidates[0]
    return max(dated, key=lambda f: dates[f])


def _latest_message_dates(conn, folders):
    latest = {}
    for folder in folders:
        newest = conn.execute(
            "SELECT MAX(COALESCE(internal_date_ts, date_ts)) FROM messages WHERE folder = ? AND COALESCE(internal_date_ts, date_ts) IS NOT NULL",
            (folder,),
        ).fetchone()
        if newest and newest[0] is not None:
            latest[folder] = int(newest[0])
            continue
        cursor = conn.execute("SELECT date FROM messages WHERE folder = ?", (folder,))
        best = None
        for (date_str,) in cursor:
            ts = _parse_date(date_str)
            if ts is not None and (best is None or ts > best):
                best = ts
        if best is not None:
            latest[folder] = best
    return latest


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        return None
    if not dt:
        return None
    dt = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    return int(dt.timestamp())
