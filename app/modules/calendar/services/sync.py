"""Headless-capable CalDAV sync for the calendar cache.

Extracted from the controller so background workers (U24.31) can drive the
same sync path with explicit credentials instead of a browser session.
"""

import logging
import uuid

from app.modules.calendar.services import cache_db, caldav

logger = logging.getLogger(__name__)


def derive_default_calendar_name(username: str) -> str:
    local_part = username.split("@")[0] if "@" in username else username
    parts = local_part.replace(".", " ").replace("_", " ").replace("-", " ").split()
    if parts:
        return parts[0][0].upper() + parts[0][1:].lower() if len(parts[0]) > 1 else parts[0].upper()
    return local_part.capitalize()


def sync_calendars_and_events(conn, account, base_url: str, password: str):
    """Discover remote calendars and mirror events into the local cache.

    ``base_url`` is the CalDAV base URL (scheme://host:port). Raises on
    connection/auth failure; the caller decides how to surface it.
    """
    s, remote_calendars = caldav.discover_calendars(base_url, account.username, password)

    if not remote_calendars:
        cal_name = derive_default_calendar_name(account.username)
        try:
            cal_url = caldav.create_calendar(
                s, base_url, account.username, name=cal_name, color="#4285f4"
            )
            remote_calendars = [
                {"url": cal_url, "displayname": cal_name, "color": "#4285f4", "sync_token": None}
            ]
        except Exception:
            logger.exception("failed to auto-create default calendar for %s", account.username)
            return

    from app.modules.calendar.services.icalendar import extract_uid

    local_cal_uids = set()
    for idx, rcal in enumerate(remote_calendars):
        cal_uid = uuid.uuid5(uuid.NAMESPACE_URL, rcal["url"]).hex
        local_cal_uids.add(cal_uid)
        is_default = idx == 0 and cache_db.count_calendars(conn) == 0
        cal_id = cache_db.upsert_calendar(
            conn,
            cal_uid,
            rcal["url"],
            displayname=rcal.get("displayname", "Calendar"),
            color=rcal.get("color", "#4285f4"),
            is_default=is_default,
        )

        try:
            remote_events = caldav.list_events(s, rcal["url"])
            remote_uids = set()
            for href, etag, ical_text in remote_events:
                uid = extract_uid(ical_text)
                if uid:
                    remote_uids.add(uid)
                    cache_db.upsert_event(conn, uid, href, etag, cal_id, ical_text)

            local_rows = conn.execute(
                "SELECT uid FROM calendar_events WHERE calendar_id = ?", (cal_id,)
            ).fetchall()
            for (local_uid,) in local_rows:
                if local_uid not in remote_uids:
                    cache_db.delete_event_by_uid(conn, local_uid, cal_id)
        except Exception:
            logger.exception("failed to sync events for calendar %s", rcal["url"])

        cache_db.set_sync_state(conn, rcal["url"], sync_token=rcal.get("sync_token"))

    local_cals = cache_db.get_all_calendars(conn)
    for lc in local_cals:
        if lc["uid"] not in local_cal_uids:
            cache_db.delete_calendar_by_id(conn, lc["id"])
