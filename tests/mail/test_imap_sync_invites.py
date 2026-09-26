from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

SAMPLE_ICS_REQUEST = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REQUEST\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:invite-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "DTEND:20260615T110000Z\r\n"
    "ORGANIZER;CN=Alice:mailto:alice@example.com\r\n"
    "ATTENDEE;CN=Bob;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SAMPLE_ICS_REPLY = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//Test//EN\r\n"
    "METHOD:REPLY\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:invite-uid@example.com\r\n"
    "SUMMARY:Team Meeting\r\n"
    "DTSTART:20260615T100000Z\r\n"
    "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED;RSVP=FALSE:mailto:bob@example.com\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _message_with_ics(subject, ical_text, method):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg.attach(MIMEText("plain body", "plain"))
    ics_part = MIMEText(ical_text, "calendar", "utf-8")
    ics_part.replace_header("Content-Type", f"text/calendar; method={method}; charset=utf-8")
    msg.attach(ics_part)
    return msg


class TestCheckImipReplyInviteHook:
    def test_request_invite_triggers_invite_processing(self):
        from app.modules.mail.services.imap_sync import _check_imip_reply

        account = MagicMock()
        msg = _message_with_ics("Invite", SAMPLE_ICS_REQUEST, "REQUEST")
        with (
            patch("app.modules.mail.services.imap_sync._process_invite_request") as mock_invite,
            patch(
                "app.modules.calendar.services.reply_processor.process_incoming_reply"
            ) as mock_reply,
        ):
            _check_imip_reply(account, msg, "alice@example.com", message_id=42)

        mock_invite.assert_called_once()
        call_args = mock_invite.call_args
        assert call_args.args[0] is account
        assert "invite-uid@example.com" in call_args.args[1]
        assert call_args.args[2] == 42
        mock_reply.assert_not_called()

    def test_reply_invite_keeps_reply_processing(self):
        from app.modules.mail.services.imap_sync import _check_imip_reply

        account = MagicMock()
        msg = _message_with_ics("Re: Invite", SAMPLE_ICS_REPLY, "REPLY")
        with patch("app.modules.mail.services.imap_sync._process_invite_request") as mock_invite:
            _check_imip_reply(account, msg, "bob@example.com")

        mock_invite.assert_not_called()

    def test_plain_message_is_ignored(self):
        from app.modules.mail.services.imap_sync import _check_imip_reply

        account = MagicMock()
        msg = MIMEMultipart()
        msg["Subject"] = "Hello"
        msg.attach(MIMEText("plain body", "plain"))
        with patch("app.modules.mail.services.imap_sync._process_invite_request") as mock_invite:
            _check_imip_reply(account, msg, "alice@example.com")

        mock_invite.assert_not_called()


class TestProcessInviteRequestWiring:
    def test_opens_calendar_cache_and_delegates(self):
        from app.modules.mail.services import imap_sync

        account = MagicMock()
        account.customer_id = 1
        mock_conn = MagicMock()
        with (
            patch(
                "app.modules.calendar.services.cache.get_cache_path", return_value="/tmp/cal.db"
            ) as mock_path,
            patch("app.modules.mail.services.imap_sync.get_user_key", return_value="0" * 64),
            patch(
                "app.modules.calendar.services.cache_db.open_cache", return_value=mock_conn
            ) as mock_open,
            patch(
                "app.modules.calendar.services.invite_processor.process_incoming_invite"
            ) as mock_process,
        ):
            imap_sync._process_invite_request(account, SAMPLE_ICS_REQUEST, message_id=42)

        mock_path.assert_called_once_with(account)
        mock_open.assert_called_once()
        mock_process.assert_called_once_with(mock_conn, SAMPLE_ICS_REQUEST, account, message_id=42)
        mock_conn.close.assert_called_once()

    def test_missing_cache_path_is_noop(self):
        from app.modules.mail.services import imap_sync

        account = MagicMock()
        with (
            patch("app.modules.calendar.services.cache.get_cache_path", return_value=None),
            patch(
                "app.modules.calendar.services.invite_processor.process_incoming_invite"
            ) as mock_process,
        ):
            imap_sync._process_invite_request(account, SAMPLE_ICS_REQUEST)

        mock_process.assert_not_called()

    def test_processor_exception_does_not_propagate(self):
        from app.modules.mail.services import imap_sync

        account = MagicMock()
        account.customer_id = 1
        mock_conn = MagicMock()
        with (
            patch("app.modules.calendar.services.cache.get_cache_path", return_value="/tmp/cal.db"),
            patch("app.modules.mail.services.imap_sync.get_user_key", return_value="0" * 64),
            patch("app.modules.calendar.services.cache_db.open_cache", return_value=mock_conn),
            patch(
                "app.modules.calendar.services.invite_processor.process_incoming_invite",
                side_effect=Exception("boom"),
            ),
        ):
            imap_sync._process_invite_request(account, SAMPLE_ICS_REQUEST)

        mock_conn.close.assert_called_once()
