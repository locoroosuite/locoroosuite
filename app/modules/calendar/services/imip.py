import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders as email_encoders
from datetime import datetime, timezone as dt_timezone

from app.shared.icalendar import generate_icalendar

logger = logging.getLogger(__name__)


def _format_imip_datetime(dt_str, event_tz=None):
    if not dt_str:
        return "", ""
    try:
        dt = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return dt_str, ""
    tz_label = ""
    if dt.tzinfo is None:
        if event_tz:
            try:
                from zoneinfo import ZoneInfo
                dt = dt.replace(tzinfo=ZoneInfo(event_tz))
                tz_label = event_tz
            except Exception:
                dt = dt.replace(tzinfo=dt_timezone.utc)
                tz_label = "UTC"
        else:
            tz_label = "UTC"
    else:
        tz_name = getattr(dt.tzinfo, "key", None)
        tz_label = tz_name or str(dt.tzinfo)
    formatted = dt.strftime("%a, %b %d, %Y at %I:%M %p")
    return formatted, tz_label


def _format_imip_when(dtstart_str, dtend_str, event_tz=None):
    start_fmt, start_tz = _format_imip_datetime(dtstart_str, event_tz)
    end_fmt, _ = _format_imip_datetime(dtend_str, event_tz)
    if not start_fmt:
        return ""
    tz_label = f" ({start_tz})" if start_tz else ""
    if end_fmt:
        start_date = start_fmt.split(" at ")[0]
        end_date = end_fmt.split(" at ")[0]
        if start_date == end_date:
            start_time = start_fmt.split(" at ")[1]
            end_time = end_fmt.split(" at ")[1]
            return f"{start_date} {start_time} – {end_time}{tz_label}"
        return f"{start_fmt} – {end_fmt}{tz_label}"
    return f"{start_fmt}{tz_label}"


def build_imip_email(from_addr, organizer_name, attendees, event_data, method, uid=None):
    if method == "CANCEL":
        subject = f"Cancelled: {event_data.get('summary', 'Event')}"
        body_text = (
            f"This event has been cancelled.\n\n"
            f"Title: {event_data.get('summary', '')}\n"
        )
    elif method == "REPLY":
        subject = f"Re: {event_data.get('summary', 'Event')}"
        reply_attendee = event_data.get("reply_attendee", {})
        partstat = event_data.get("reply_partstat", "ACCEPTED")
        status_label = {"ACCEPTED": "accepted", "TENTATIVE": "tentatively accepted", "DECLINED": "declined"}.get(partstat, "responded to")
        attendee_name = reply_attendee.get("cn", reply_attendee.get("email", "Someone"))
        body_text = f"{attendee_name} has {status_label} the invitation.\n\nTitle: {event_data.get('summary', '')}\n"
    else:
        subject = f"Invitation: {event_data.get('summary', 'Event')}"
        dtstart = event_data.get("dtstart", "")
        dtend = event_data.get("dtend", "")
        event_tz = event_data.get("timezone")
        location = event_data.get("location", "")
        body_text = f"You have been invited to an event.\n\nTitle: {event_data.get('summary', '')}\n"
        when_display = _format_imip_when(dtstart, dtend, event_tz)
        if when_display:
            body_text += f"When: {when_display}\n"
        if location:
            body_text += f"Where: {location}\n"

    ical_data = dict(event_data)
    ical_data["method"] = method
    if method == "REPLY":
        ical_data["attendees"] = [event_data.get("reply_attendee", {})]
    ical_text = generate_icalendar(ical_data, uid=uid)

    msg_root = MIMEMultipart("mixed")
    msg_root["From"] = from_addr
    if method == "REPLY":
        organizer_email = event_data.get("organizer", {}).get("email", "")
        msg_root["To"] = organizer_email
    else:
        msg_root["To"] = ", ".join(a.get("email", "") for a in attendees)
    msg_root["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain", "utf-8"))

    ical_part = MIMEText(ical_text, "calendar; method=" + method, "utf-8")
    ical_part.replace_header("Content-Type", f"text/calendar; charset=utf-8; method={method}")
    alt.attach(ical_part)
    msg_root.attach(alt)

    ics_attachment = MIMEBase("application", "ics")
    ics_attachment.set_payload(ical_text.encode("utf-8"))
    email_encoders.encode_base64(ics_attachment)
    ics_attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename="invite.ics",
    )
    msg_root.attach(ics_attachment)

    return msg_root.as_bytes(), subject


def send_imip_email(domain, account, event_data, method, attendees, uid=None):
    from app.modules.mail.services.smtp_client import smtp_connect, smtp_login, smtp_send
    from app.modules.mail.services.secrets import decrypt_with_key
    from app.shared.keys import get_user_key

    key = get_user_key(account.customer_id)
    if not key:
        raise RuntimeError("Session key unavailable.")
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    if not secret:
        raise RuntimeError("Credentials unavailable.")

    from_addr = account.email_address
    organizer_name = account.email_address

    msg_bytes, subject = build_imip_email(
        from_addr, organizer_name, attendees, event_data, method, uid=uid,
    )

    if method == "REPLY":
        recipients = [event_data.get("organizer", {}).get("email", "")]
    else:
        recipients = [a.get("email", "") for a in attendees if a.get("email")]

    recipients = [r.strip() for r in recipients if r.strip()]
    if not recipients:
        logger.warning("imip send skipped: no recipients method=%s uid=%s", method, uid)
        return

    server = None
    try:
        server = smtp_connect(domain.smtp_host, domain.smtp_port, domain.smtp_tls_mode)
        smtp_login(server, account.username, password=secret)
        smtp_send(server, from_addr, recipients, msg_bytes)
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

    logger.info("imip sent method=%s uid=%s recipients=%s", method, uid, recipients)


def build_reply_imip_email(from_addr, attendee_data, organizer_data, event_data, partstat, uid=None):
    reply_event_data = dict(event_data)
    reply_event_data["reply_attendee"] = attendee_data
    reply_event_data["reply_partstat"] = partstat
    reply_event_data["organizer"] = organizer_data
    attendee_data_reply = dict(attendee_data)
    attendee_data_reply["partstat"] = partstat
    attendee_data_reply["rsvp"] = "FALSE"
    reply_event_data["attendees"] = [attendee_data_reply]

    return build_imip_email(
        from_addr, from_addr, [], reply_event_data, "REPLY", uid=uid,
    )


def send_reply_imip(domain, account, attendee_data, organizer_data, event_data, partstat, uid=None):
    from app.modules.mail.services.smtp_client import smtp_connect, smtp_login, smtp_send
    from app.modules.mail.services.secrets import decrypt_with_key
    from app.shared.keys import get_user_key

    key = get_user_key(account.customer_id)
    if not key:
        raise RuntimeError("Session key unavailable.")
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    if not secret:
        raise RuntimeError("Credentials unavailable.")

    from_addr = account.email_address
    msg_bytes, subject = build_reply_imip_email(
        from_addr, attendee_data, organizer_data, event_data, partstat, uid=uid,
    )

    recipients = [organizer_data.get("email", "")]
    recipients = [r.strip() for r in recipients if r.strip()]
    if not recipients:
        logger.warning("imip reply skipped: no organizer email uid=%s", uid)
        return

    server = None
    try:
        server = smtp_connect(domain.smtp_host, domain.smtp_port, domain.smtp_tls_mode)
        smtp_login(server, account.username, password=secret)
        smtp_send(server, from_addr, recipients, msg_bytes)
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

    logger.info("imip reply sent partstat=%s uid=%s organizer=%s", partstat, uid, recipients)
