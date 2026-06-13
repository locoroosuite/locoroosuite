import json
import logging
import re

from app.shared.icalendar import parse_icalendar, extract_uid

logger = logging.getLogger(__name__)


def process_incoming_reply(calendar_cache_conn, ical_text, sender_email, account=None):
    parsed = parse_icalendar(ical_text)
    if not parsed:
        return False

    method = (parsed.get("method") or "").upper()
    if method != "REPLY":
        return False

    uid = parsed.get("uid") or extract_uid(ical_text)
    if not uid:
        return False

    attendees = parsed.get("attendees", [])
    if not attendees:
        return False

    reply_attendee = attendees[0]
    reply_email = reply_attendee.get("email", "").lower()
    partstat = reply_attendee.get("partstat", "NEEDS-ACTION")

    from app.modules.calendar.services import cache_db

    event = cache_db.get_event_by_uid(calendar_cache_conn, uid)
    if not event:
        logger.debug("reply processing: event uid=%s not found in cache", uid)
        return False

    raw_attendees = event.get("attendees")
    event_attendees = []
    if isinstance(raw_attendees, str):
        try:
            event_attendees = json.loads(raw_attendees)
        except (ValueError, TypeError):
            event_attendees = []
    elif isinstance(raw_attendees, list):
        event_attendees = raw_attendees

    updated = False
    for att in event_attendees:
        if isinstance(att, dict) and att.get("email", "").lower() == reply_email:
            att["partstat"] = partstat
            att["rsvp"] = "FALSE"
            updated = True
            break

    if not updated:
        logger.debug("reply processing: attendee %s not found in event uid=%s", reply_email, uid)
        return False

    attendees_json = json.dumps(event_attendees)

    raw_ical = event.get("raw_ical") or ""
    patched_ical = _patch_raw_ical_attendee(raw_ical, reply_email, partstat)

    calendar_cache_conn.execute(
        "UPDATE calendar_events SET attendees = ?, raw_ical = ?, updated_at = ? WHERE uid = ?",
        (attendees_json, patched_ical, cache_db._now(), uid),
    )
    calendar_cache_conn.commit()
    logger.info("reply processed: uid=%s attendee=%s partstat=%s", uid, reply_email, partstat)

    if account and event.get("href"):
        try:
            _push_to_caldav(account, event, patched_ical)
        except Exception:
            logger.warning(
                "reply caldav push failed uid=%s attendee=%s (cache updated)",
                uid, reply_email, exc_info=True,
            )

    return True


def _patch_raw_ical_attendee(raw_ical, email, partstat):
    if not raw_ical:
        return raw_ical

    email_escaped = re.escape(email)
    pattern = re.compile(
        r"(ATTENDEE[^:]*:mailto:" + email_escaped + r")",
        re.IGNORECASE,
    )

    def _replace(match):
        line = match.group(0)
        if re.search(r"PARTSTAT=[^;:]+", line, re.IGNORECASE):
            line = re.sub(r"PARTSTAT=[^;:]+", f"PARTSTAT={partstat}", line, flags=re.IGNORECASE)
        else:
            line = line.replace(
                f":mailto:{email}",
                f";PARTSTAT={partstat}:mailto:{email}",
            )
            line = line.replace(
                f":mailto:{email}",
                f";PARTSTAT={partstat}:mailto:{email}",
            )
        return line

    return pattern.sub(_replace, raw_ical)


def _push_to_caldav(account, event, patched_ical):
    from app.shared.db import db
    from app.shared.models.core import Domain
    from app.shared.keys import get_user_key
    from app.modules.mail.services.secrets import decrypt_with_key
    from app.modules.calendar.services import caldav

    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.caldav_host:
        return

    key = get_user_key(account.customer_id)
    if not key:
        return

    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    if not secret:
        return

    scheme = "https" if domain.caldav_use_tls else "http"
    base_url = f"{scheme}://{domain.caldav_host}:{domain.caldav_port or 5232}"

    s = caldav._make_session(account.username, secret)
    caldav.update_event(s, event["href"], patched_ical, event.get("etag"))
    logger.info("reply pushed to caldav: uid=%s", event.get("uid"))
