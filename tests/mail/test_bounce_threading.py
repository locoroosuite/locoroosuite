from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


from app.modules.mail.services.imap_sync import _extract_bounce_info, _prepare_message_args


def _make_bounce_email(
    original_subject="Re: Project discussion",
    original_message_id="<original123@test.com>",
    original_in_reply_to="<parent456@test.com>",
    original_references="<root@test.com> <parent456@test.com>",
    bounce_reason="smtp; 550 5.7.606 Access denied, banned sending IP [1.2.3.4]",
    failed_recipient="target@example.com",
):
    outer = MIMEMultipart("report")
    outer["From"] = "MAILER-DAEMON@example.com"
    outer["To"] = "user@example.com"
    outer["Subject"] = "Undelivered Mail Returned to Sender"
    outer["Message-ID"] = "<bounce2024@example.com>"
    outer["Date"] = "Mon, 18 May 2026 17:40:23 +1000"

    notification = MIMEText(
        "This is the mail system at host mail.example.com.\n\n"
        "I'm sorry to have to inform you that your message could not\n"
        "be delivered to one or more recipients.\n",
        _subtype="plain",
    )
    notification["Content-Description"] = "Notification"
    outer.attach(notification)

    dsn = Message()
    dsn["Content-Type"] = "message/delivery-status"
    dsn["Reporting-MTA"] = "dns; mail.example.com"
    per_recipient = Message()
    per_recipient["Final-Recipient"] = f"rfc822; {failed_recipient}"
    per_recipient["Action"] = "failed"
    per_recipient["Status"] = "5.7.606"
    per_recipient["Diagnostic-Code"] = bounce_reason
    dsn.set_payload([per_recipient])
    outer.attach(dsn)

    original = Message()
    original["Content-Type"] = "message/rfc822"
    original["Content-Description"] = "Undelivered Message"
    inner = Message()
    inner["From"] = "user@example.com"
    inner["To"] = f"Target <{failed_recipient}>"
    inner["Subject"] = original_subject
    inner["Message-ID"] = original_message_id
    if original_in_reply_to:
        inner["In-Reply-To"] = original_in_reply_to
    if original_references:
        inner["References"] = original_references
    inner["Date"] = "Mon, 18 May 2026 17:40:19 +1000"
    original.set_payload([inner])
    outer.attach(original)

    return outer


def _make_regular_email():
    msg = Message()
    msg["From"] = "sender@example.com"
    msg["To"] = "user@example.com"
    msg["Subject"] = "Regular email"
    msg["Message-ID"] = "<regular@test.com>"
    msg["In-Reply-To"] = "<parent@test.com>"
    msg["References"] = "<root@test.com> <parent@test.com>"
    msg["Date"] = "Mon, 18 May 2026 18:00:00 +1000"
    msg.set_payload("Hello world")
    return msg


class TestExtractBounceInfo:
    def test_detects_postfix_bounce(self):
        msg = _make_bounce_email()
        result = _extract_bounce_info(msg)
        assert result is not None
        assert result["is_bounce"] is True
        assert result["original_message_id"] == "<original123@test.com>"
        assert result["original_in_reply_to"] == "<parent456@test.com>"
        assert result["original_references"] == "<root@test.com> <parent456@test.com>"
        assert result["original_subject"] == "Re: Project discussion"
        assert "5.7.606" in result["bounce_reason"]

    def test_non_bounce_returns_none(self):
        msg = _make_regular_email()
        result = _extract_bounce_info(msg)
        assert result is None

    def test_mailer_daemon_non_dsn(self):
        msg = Message()
        msg["From"] = "MAILER-DAEMON@example.com"
        msg["To"] = "user@test.com"
        msg["Subject"] = "Some notification"
        msg["Message-ID"] = "<md@test.com>"
        msg.set_payload("Not a DSN")
        result = _extract_bounce_info(msg)
        assert result is None

    def test_dsn_without_original_headers(self):
        outer = MIMEMultipart("report")
        outer["From"] = "MAILER-DAEMON@test.com"
        outer["Subject"] = "Undelivered"
        outer["Message-ID"] = "<bounce@test.com>"
        notification = MIMEText("Failed", _subtype="plain")
        outer.attach(notification)
        dsn = Message()
        dsn["Content-Type"] = "message/delivery-status"
        per_recipient = Message()
        per_recipient["Action"] = "failed"
        per_recipient["Diagnostic-Code"] = "smtp; 550 error"
        dsn.set_payload([per_recipient])
        outer.attach(dsn)
        result = _extract_bounce_info(outer)
        assert result is None

    def test_bounce_with_no_diagnostic_code(self):
        outer = MIMEMultipart("report")
        outer["From"] = "MAILER-DAEMON@test.com"
        outer["Subject"] = "Undelivered"
        outer["Message-ID"] = "<b@test.com>"
        outer["Date"] = "Mon, 18 May 2026 18:00:00 +0000"
        notification = MIMEText("Failed", _subtype="plain")
        outer.attach(notification)
        dsn = Message()
        dsn["Content-Type"] = "message/delivery-status"
        per_recipient = Message()
        per_recipient["Action"] = "failed"
        dsn.set_payload([per_recipient])
        outer.attach(dsn)
        result = _extract_bounce_info(outer)
        assert result is None

    def test_google_style_bounce(self):
        outer = MIMEMultipart("report")
        outer["From"] = "Mail Delivery Subsystem <mailer-daemon@googlemail.com>"
        outer["To"] = "user@example.com"
        outer["Subject"] = "Delivery Status Notification (Failure)"
        outer["Message-ID"] = "<googlebounce@test.com>"
        outer["Date"] = "Mon, 18 May 2026 18:00:00 +0000"

        dsn = Message()
        dsn["Content-Type"] = "message/delivery-status"
        per_recipient = Message()
        per_recipient["Final-Recipient"] = "rfc822; bad@example.com"
        per_recipient["Action"] = "failed"
        per_recipient["Status"] = "5.1.1"
        per_recipient["Diagnostic-Code"] = "smtp; 550 5.1.1 User unknown"
        dsn.set_payload([per_recipient])
        outer.attach(dsn)

        original = Message()
        original["Content-Type"] = "message/rfc822"
        inner = Message()
        inner["Subject"] = "Re: Job application"
        inner["Message-ID"] = "<sent-msg@test.com>"
        inner["In-Reply-To"] = "<their-msg@test.com>"
        inner["References"] = "<start@test.com> <their-msg@test.com>"
        inner["From"] = "user@example.com"
        inner["To"] = "bad@example.com"
        original.set_payload([inner])
        outer.attach(original)

        result = _extract_bounce_info(outer)
        assert result is not None
        assert result["is_bounce"] is True
        assert result["original_subject"] == "Re: Job application"
        assert "User unknown" in result["bounce_reason"]


class TestPrepareMessageArgs:
    def test_regular_message(self):
        msg = _make_regular_email()
        args = _prepare_message_args(msg)
        assert args["is_bounce"] is False
        assert args["bounce_reason"] is None
        assert args["original_subject"] is None
        assert args["subject"] == "Regular email"
        assert args["message_id"] == "<regular@test.com>"
        assert args["thread_id"] is None

    def test_bounce_message_thread_id(self):
        msg = _make_bounce_email()
        args = _prepare_message_args(msg)
        assert args["is_bounce"] is True
        assert args["thread_id"] == "root@test.com"
        assert args["original_subject"] == "Re: Project discussion"
        assert args["subject"] == "Undelivered Mail Returned to Sender"
        assert "5.7.606" in args["bounce_reason"]

    def test_bounce_with_only_in_reply_to(self):
        msg = _make_bounce_email(
            original_references=None,
            original_in_reply_to="<parent456@test.com>",
        )
        args = _prepare_message_args(msg)
        assert args["is_bounce"] is True
        assert args["thread_id"] == "parent456@test.com"

    def test_bounce_with_no_threading_headers(self):
        msg = _make_bounce_email(
            original_in_reply_to=None,
            original_references=None,
            original_message_id="<orphan@test.com>",
        )
        args = _prepare_message_args(msg)
        assert args["is_bounce"] is True
        assert args["thread_id"] == "orphan@test.com"


def _make_thread_db(tmp_path):
    from app.modules.mail.services.cache_db import init_cache_schema
    import sqlcipher3

    db_path = str(tmp_path / "test.db")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
    init_cache_schema(conn)
    return conn


class TestBounceThreading:
    def test_bounce_grouped_with_original_thread(self, tmp_path):
        from app.modules.mail.services.cache_db import upsert_message
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Re: Project discussion", "a@b.com", "user@example.com",
            "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
            "<msg1@test.com>", thread_id="root@test.com",
        )
        upsert_message(
            conn, "2", "INBOX", "Re: Project discussion", "c@d.com", "user@example.com",
            "Mon, 1 Jan 2024 11:00:00 +0000", [], "snip2", "body2", False,
            "<msg2@test.com>", thread_id="root@test.com",
        )
        upsert_message(
            conn, "3", "INBOX", "Undelivered Mail Returned to Sender",
            "MAILER-DAEMON@example.com", "user@example.com",
            "Mon, 1 Jan 2024 12:00:00 +0000", [], "Delivery failed", "bounce body", False,
            "<bounce@test.com>", thread_id="root@test.com",
            is_bounce=True, bounce_reason="smtp; 550 error",
            original_subject="Re: Project discussion",
        )

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 3
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 3
        bounce_msgs = [m for m in group if m.get("is_bounce")]
        assert len(bounce_msgs) == 1
        assert bounce_msgs[0]["is_bounce"] is True
        assert "550 error" in bounce_msgs[0]["bounce_reason"]

    def test_bounce_displays_original_subject(self, tmp_path):
        from app.modules.mail.services.cache_db import upsert_message
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Re: Project discussion", "a@b.com", "user@example.com",
            "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
            "<msg1@test.com>", thread_id="root@test.com",
        )
        upsert_message(
            conn, "2", "INBOX", "Undelivered Mail Returned to Sender",
            "MAILER-DAEMON@example.com", "user@example.com",
            "Mon, 1 Jan 2024 11:00:00 +0000", [], "Delivery failed", "bounce body", False,
            "<bounce@test.com>", thread_id="root@test.com",
            is_bounce=True, bounce_reason="smtp; 550 error",
            original_subject="Re: Project discussion",
        )

        threads, pagination = _build_threads(conn, "INBOX")
        group = list(threads.values())[0]
        bounce_msgs = [m for m in group if m.get("is_bounce")]
        assert len(bounce_msgs) == 1
        assert bounce_msgs[0]["subject"] == "Re: Project discussion"

    def test_bounce_standalone_without_original_thread(self, tmp_path):
        from app.modules.mail.services.cache_db import upsert_message
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Undelivered Mail Returned to Sender",
            "MAILER-DAEMON@example.com", "user@example.com",
            "Mon, 1 Jan 2024 10:00:00 +0000", [], "failed", "bounce", False,
            "<bounce@test.com>", thread_id="standalone@test.com",
            is_bounce=True, bounce_reason="smtp; 550 error",
            original_subject="Re: Something else",
        )

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 1
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert group[0]["is_bounce"] is True


class TestBounceThreadDetail:
    def test_bounce_in_thread_detail(self, tmp_path):
        from app.modules.mail.services.cache_db import upsert_message
        from app.modules.mail.controllers.helpers import _load_thread_for_detail

        conn = _make_thread_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Re: Project discussion", "a@b.com", "user@example.com",
            "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
            "<msg1@test.com>", thread_id="root@test.com",
        )
        upsert_message(
            conn, "2", "INBOX", "Undelivered Mail Returned to Sender",
            "MAILER-DAEMON@example.com", "user@example.com",
            "Mon, 1 Jan 2024 11:00:00 +0000", [], "failed", "bounce body", False,
            "<bounce@test.com>", thread_id="root@test.com",
            is_bounce=True, bounce_reason="smtp; 550 Access denied",
            original_subject="Re: Project discussion",
        )

        result = _load_thread_for_detail(conn, "root@test.com", 1, "Re: Project discussion")
        assert len(result) == 2
        bounce_tm = [tm for tm in result if tm.get("is_bounce")]
        assert len(bounce_tm) == 1
        assert bounce_tm[0]["is_bounce"] is True
        assert "Access denied" in bounce_tm[0]["bounce_reason"]
        assert bounce_tm[0]["subject"] == "Re: Project discussion"

    def test_bounce_as_current_message(self, tmp_path):
        from app.modules.mail.services.cache_db import upsert_message
        from app.modules.mail.controllers.helpers import _load_thread_for_detail

        conn = _make_thread_db(tmp_path)
        upsert_message(
            conn, "1", "INBOX", "Re: Project discussion", "a@b.com", "user@example.com",
            "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
            "<msg1@test.com>", thread_id="root@test.com",
        )
        upsert_message(
            conn, "2", "INBOX", "Undelivered Mail Returned to Sender",
            "MAILER-DAEMON@example.com", "user@example.com",
            "Mon, 1 Jan 2024 11:00:00 +0000", [], "failed", "bounce body", False,
            "<bounce@test.com>", thread_id="root@test.com",
            is_bounce=True, bounce_reason="smtp; 550 error",
            original_subject="Re: Project discussion",
        )

        result = _load_thread_for_detail(conn, "root@test.com", 2, "Re: Project discussion")
        bounce_tm = [tm for tm in result if tm["id"] == 2][0]
        assert bounce_tm["is_bounce"] is True
        assert bounce_tm["is_current"] is True


class TestBounceSchema:
    def test_bounce_columns_exist(self, tmp_path):
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        from app.modules.mail.services.cache_db import init_cache_schema
        init_cache_schema(conn)

        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "is_bounce" in columns
        assert "bounce_reason" in columns
        assert "original_subject" in columns

    def test_upsert_and_retrieve_bounce(self, tmp_path):
        from app.modules.mail.services.cache_db import upsert_message
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        from app.modules.mail.services.cache_db import init_cache_schema
        init_cache_schema(conn)

        upsert_message(
            conn, "1", "INBOX", "Undelivered Mail Returned to Sender",
            "MAILER-DAEMON@test.com", "user@test.com",
            "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip", "body", False,
            "<bounce@test.com>", thread_id="orig@test.com",
            is_bounce=True, bounce_reason="smtp; 550 error",
            original_subject="Re: Original thread",
        )

        row = conn.execute(
            "SELECT is_bounce, bounce_reason, original_subject FROM messages WHERE id = 1"
        ).fetchone()
        assert row[0] == 1
        assert "550 error" in row[1]
        assert row[2] == "Re: Original thread"

    def test_non_bounce_defaults(self, tmp_path):
        from app.modules.mail.services.cache_db import upsert_message
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        from app.modules.mail.services.cache_db import init_cache_schema
        init_cache_schema(conn)

        upsert_message(
            conn, "1", "INBOX", "Regular email", "a@b.com", "c@d.com",
            "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip", "body", False,
            "<msg@test.com>",
        )

        row = conn.execute(
            "SELECT is_bounce, bounce_reason, original_subject FROM messages WHERE id = 1"
        ).fetchone()
        assert row[0] == 0
        assert row[1] is None
        assert row[2] is None
