import logging

from app.shared.icalendar import extract_uid, generate_icalendar, parse_icalendar

logger = logging.getLogger(__name__)


def process_incoming_invite(calendar_cache_conn, ical_text, account, message_id=None):
    """Auto-import a METHOD:REQUEST invitation as a silent Tentative event (U12.39d).

    The event is created in the user's default (first) calendar with the account's
    attendee PARTSTAT=TENTATIVE and RSVP=TRUE. No METHOD:REPLY is sent; the
    organizer is only notified when the user explicitly responds (U12.36).
    Returns True if the event was imported.
    """
    parsed = parse_icalendar(ical_text)
    if not parsed:
        return False

    method = (parsed.get("method") or "").upper()
    if method != "REQUEST":
        return False

    uid = parsed.get("uid") or extract_uid(ical_text)
    if not uid:
        return False

    from app.modules.calendar.services import cache_db

    # Never downgrade an explicit response: skip if the UID is already present.
    if cache_db.get_event_by_uid(calendar_cache_conn, uid):
        logger.debug("invite processing: uid=%s already in calendar, skipping", uid)
        return False

    my_email = (getattr(account, "email_address", "") or "").lower()
    if not my_email:
        return False

    attendees = parsed.get("attendees") or []
    is_attendee = any(
        isinstance(att, dict) and (att.get("email") or "").lower() == my_email for att in attendees
    )
    if not is_attendee:
        logger.debug("invite processing: account not an attendee uid=%s", uid)
        return False

    calendars = cache_db.get_all_calendars(calendar_cache_conn)
    if not calendars:
        logger.warning(
            "invite processing: no calendars cached, skipping auto-tentative uid=%s", uid
        )
        return False
    cal = calendars[0]

    updated_attendees = []
    for att in attendees:
        if isinstance(att, dict) and (att.get("email") or "").lower() == my_email:
            updated_attendees.append({**att, "partstat": "TENTATIVE", "rsvp": "TRUE"})
        else:
            updated_attendees.append(att)

    import_data = {k: v for k, v in parsed.items() if k not in ("method", "attendees")}
    import_data["method"] = None
    import_data["attendees"] = updated_attendees
    clean_ical = generate_icalendar(import_data, uid=uid)

    pushed = _push_to_caldav(account, cal["href"], clean_ical, uid)
    if not pushed:
        return False
    href, etag = pushed

    event_id = cache_db.upsert_event(calendar_cache_conn, uid, href, etag, cal["id"], clean_ical)

    if message_id is not None:
        cache_db.set_event_source_email(calendar_cache_conn, event_id, message_id, account.id)

    logger.info("invite auto-imported as tentative: uid=%s calendar_id=%s", uid, cal["id"])
    return True


def _push_to_caldav(account, calendar_href, clean_ical, uid):
    from app.modules.calendar.services import caldav
    from app.modules.mail.services.secrets import decrypt_with_key
    from app.shared.db import db
    from app.shared.keys import get_user_key
    from app.shared.models.core import Domain

    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.caldav_host:
        logger.warning("invite push: caldav not configured for domain, uid=%s", uid)
        return None

    key = get_user_key(account.customer_id)
    if not key:
        logger.warning("invite push: no user key, uid=%s", uid)
        return None

    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    if not secret:
        logger.warning("invite push: no decrypted secret, uid=%s", uid)
        return None

    try:
        s = caldav._make_session(account.username, secret)
        href, etag = caldav.create_event(s, calendar_href, clean_ical, uid=uid)
    except Exception:
        logger.exception("invite push: caldav create failed uid=%s", uid)
        return None
    return href, etag
