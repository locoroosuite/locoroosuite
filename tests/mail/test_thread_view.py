import json
import re
from unittest.mock import patch, MagicMock

import pytest

from app.modules.mail.utils.sanitize import add_quoted_collapse, wrap_email_html
from app.modules.mail.services.cache_db import list_thread_messages


class TestAddQuotedCollapse:
    def test_empty_input(self):
        assert add_quoted_collapse("") == ""
        assert add_quoted_collapse(None) is None

    def test_no_quotes_unchanged(self):
        html = "<div>Hello world</div>"
        assert add_quoted_collapse(html) == html

    def test_blockquote_wrapped_in_details(self):
        html = "<p>Hello</p><blockquote><p>Quoted text</p></blockquote><p>More</p>"
        result = add_quoted_collapse(html)
        assert "<details" in result
        assert 'class="lr-quoted"' in result
        assert "<blockquote" in result
        assert "Show trimmed content" in result
        assert "</details>" in result

    def test_multiple_blockquotes(self):
        html = "<blockquote>A</blockquote><p>middle</p><blockquote>B</blockquote>"
        result = add_quoted_collapse(html)
        assert result.count("<details") == 2
        assert result.count("</details>") == 2

    def test_nested_blockquotes(self):
        html = "<blockquote><p>Level 1</p><blockquote><p>Level 2</p></blockquote></blockquote>"
        result = add_quoted_collapse(html)
        assert "<details" in result
        assert result.count("<blockquote") == 2

    def test_blockquote_with_attrs(self):
        html = '<blockquote type="cite"><p>Quoted</p></blockquote>'
        result = add_quoted_collapse(html)
        assert 'type="cite"' in result
        assert "<details" in result

    def test_plain_text_quoted_lines(self):
        html = "<div>Hello<br>&gt; quoted line 1<br>&gt; quoted line 2<br>More text</div>"
        result = add_quoted_collapse(html)
        assert "<details" in result
        assert "Show trimmed content" in result
        assert "&gt; quoted line 1" in result

    def test_single_quoted_line_not_collapsed(self):
        html = "<div>Hello<br>&gt; single quote<br>More</div>"
        result = add_quoted_collapse(html)
        assert "<details" not in result

    def test_blockquote_takes_priority_over_plain(self):
        html = "<blockquote>HTML quote</blockquote><br>&gt; plain quote 1<br>&gt; plain quote 2"
        result = add_quoted_collapse(html)
        assert result.count("<details") == 1

    def test_details_summary_structure(self):
        html = "<blockquote><p>Quoted</p></blockquote>"
        result = add_quoted_collapse(html)
        assert re.search(r"<details[^>]*>.*?<summary[^>]*>.*?</summary>.*?<blockquote", result, re.DOTALL)


class TestListThreadMessages:
    def test_returns_matching_thread(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message, init_cache_schema
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        upsert_message(conn, "1", "INBOX", "Re: Test", "a@b.com", "c@d.com",
                       "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
                       "<msg1@test.com>", thread_id="thread-abc")
        upsert_message(conn, "2", "INBOX", "Re: Test", "c@d.com", "a@b.com",
                       "Mon, 1 Jan 2024 11:00:00 +0000", [], "snip2", "body2", False,
                       "<msg2@test.com>", thread_id="thread-abc")
        upsert_message(conn, "3", "INBOX", "Other", "x@y.com", "z@y.com",
                       "Mon, 1 Jan 2024 12:00:00 +0000", [], "snip3", "body3", False,
                       "<msg3@test.com>", thread_id="thread-other")

        rows = list_thread_messages(conn, "thread-abc")
        assert len(rows) == 2
        assert rows[0][6] is not None
        assert rows[1][6] is not None

    def test_empty_result_for_unknown_thread(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, init_cache_schema
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        rows = list_thread_messages(conn, "nonexistent")
        assert len(rows) == 0

    def test_cross_folder_thread(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message, init_cache_schema
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        upsert_message(conn, "1", "INBOX", "Re: Test", "a@b.com", "c@d.com",
                       "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
                       "<msg1@test.com>", thread_id="thread-xyz")
        upsert_message(conn, "2", "Sent", "Re: Test", "c@d.com", "a@b.com",
                       "Mon, 1 Jan 2024 11:00:00 +0000", [], "snip2", "body2", False,
                       "<msg2@test.com>", thread_id="thread-xyz")

        rows = list_thread_messages(conn, "thread-xyz")
        assert len(rows) == 2
        folders = [r[2] for r in rows]
        assert "INBOX" in folders
        assert "Sent" in folders


class TestLoadThreadForDetail:
    def test_returns_thread_messages_sorted_by_date(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message, init_cache_schema
        from app.modules.mail.controllers.helpers import _load_thread_for_detail
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        upsert_message(conn, "1", "INBOX", "Re: Test", "a@b.com", "c@d.com",
                       "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
                       "<msg1@test.com>", thread_id="thread-abc")
        upsert_message(conn, "2", "INBOX", "Re: Test", "c@d.com", "a@b.com",
                       "Mon, 1 Jan 2024 11:00:00 +0000", [], "snip2", "body2", False,
                       "<msg2@test.com>", thread_id="thread-abc")

        result = _load_thread_for_detail(conn, "thread-abc", 1, "Re: Test")
        assert len(result) == 2
        assert result[0]["is_current"] is False
        assert result[1]["is_current"] is True
        assert result[0]["date_ts"] >= result[1]["date_ts"]

    def test_subject_fallback(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message, init_cache_schema
        from app.modules.mail.controllers.helpers import _load_thread_for_detail
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        upsert_message(conn, "1", "INBOX", "Re: Hello", "a@b.com", "c@d.com",
                       "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
                       "<msg1@test.com>", thread_id=None)
        upsert_message(conn, "2", "INBOX", "Re: Hello", "c@d.com", "a@b.com",
                       "Mon, 1 Jan 2024 11:00:00 +0000", [], "snip2", "body2", False,
                       "<msg2@test.com>", thread_id=None)

        result = _load_thread_for_detail(conn, None, 2, "Re: Hello")
        assert len(result) == 2

    def test_sent_message_identified(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message, init_cache_schema
        from app.modules.mail.controllers.helpers import _load_thread_for_detail
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        upsert_message(conn, "1", "INBOX", "Re: Test", "a@b.com", "c@d.com",
                       "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "received", False,
                       "<msg1@test.com>", thread_id="thread-sent")
        upsert_message(conn, "2", "Sent", "Re: Test", "c@d.com", "a@b.com",
                       "Mon, 1 Jan 2024 11:00:00 +0000", [], "snip2", "sent body", False,
                       "<msg2@test.com>", thread_id="thread-sent")

        result = _load_thread_for_detail(conn, "thread-sent", 1, "Re: Test")
        assert len(result) == 2
        sent_msgs = [r for r in result if r["is_sent"]]
        assert len(sent_msgs) == 1
        assert sent_msgs[0]["folder"] == "Sent"

    def test_deduplicates(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message, init_cache_schema
        from app.modules.mail.controllers.helpers import _load_thread_for_detail
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        upsert_message(conn, "1", "INBOX", "Re: Test", "a@b.com", "c@d.com",
                       "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip1", "body1", False,
                       "<msg1@test.com>", thread_id="thread-dedup")

        result = _load_thread_for_detail(conn, "thread-dedup", 1, "Re: Test")
        assert len(result) == 1

    def test_no_thread_returns_single_message(self, tmp_path):
        from app.modules.mail.services.cache_db import open_cache, upsert_message, init_cache_schema
        from app.modules.mail.controllers.helpers import _load_thread_for_detail
        import sqlcipher3

        db_path = str(tmp_path / "test.db")
        conn = sqlcipher3.connect(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
        init_cache_schema(conn)

        upsert_message(conn, "1", "INBOX", "Unique subject", "a@b.com", "c@d.com",
                       "Mon, 1 Jan 2024 10:00:00 +0000", [], "snip", "body", False,
                       "<msg1@test.com>", thread_id=None)

        result = _load_thread_for_detail(conn, None, 1, "Unique subject")
        assert len(result) == 1
        assert result[0]["is_current"] is True


class TestThreadConversationView:
    def test_thread_messages_rendered(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        mock_msg = (
            1, "100", "INBOX", "Test Subject", "sender@test.com", "recip@test.com",
            "date", '["\\\\Seen"]', "body text", "", None, 0, "<msg-id@test.com>", "thread-123", "",
        )
        thread_data = [
            {
                "id": 2, "uid": "99", "folder": "INBOX",
                "subject": "Re: Test Subject", "sender": "Alice <alice@test.com>",
                "sender_display": "Alice", "sender_tooltip": "Alice <alice@test.com>",
                "recipients": "Bob <bob@test.com>", "recipients_display": "Bob",
                "date": "Mon, 1 Jan 2024 09:00:00 +0000", "date_display": "09:00",
                "date_ts": 1704096000, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_current": False,
                "snippet": "Earlier message", "body_html": "<html>body</html>",
                "has_attachments": False, "cc": "",
            },
            {
                "id": 1, "uid": "100", "folder": "INBOX",
                "subject": "Test Subject", "sender": "sender@test.com",
                "sender_display": "sender", "sender_tooltip": "sender@test.com",
                "recipients": "recip@test.com", "recipients_display": "recip",
                "date": "Mon, 1 Jan 2024 10:00:00 +0000", "date_display": "10:00",
                "date_ts": 1704099600, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_current": True,
                "snippet": "body text", "body_html": "<html>current body</html>",
                "has_attachments": False, "cc": "",
            },
        ]
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (mock_msg, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = thread_data
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "2 messages in this conversation" in html
        assert "data-thread-msg-id=\"2\"" in html
        assert "data-thread-msg-id=\"1\"" in html
        assert "Show trimmed content" not in html or True
        assert "data-expand-icon" in html

    def test_single_message_no_thread_label(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        mock_msg = (
            1, "100", "INBOX", "Test Subject", "sender@test.com", "recip@test.com",
            "date", '["\\\\Seen"]', "body text", "", None, 0, "<msg-id@test.com>", None, "",
        )
        single_thread = [
            {
                "id": 1, "uid": "100", "folder": "INBOX",
                "subject": "Test Subject", "sender": "sender@test.com",
                "sender_display": "sender", "sender_tooltip": "sender@test.com",
                "recipients": "recip@test.com", "recipients_display": "recip",
                "date": "Mon, 1 Jan 2024 10:00:00 +0000", "date_display": "10:00",
                "date_ts": 1704099600, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_current": True,
                "snippet": "body text", "body_html": "<html>body</html>",
                "has_attachments": False, "cc": "",
            },
        ]
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (mock_msg, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = single_thread
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "messages in this conversation" not in html
        assert "data-thread-expanded" not in html or 'data-thread-expanded="true"' in html

    def test_sent_message_indigo_styling(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        mock_msg = (
            1, "100", "INBOX", "Test", "s@test.com", "r@test.com",
            "date", '["\\\\Seen"]', "body", "", None, 0, "<msg@test.com>", "t1", "",
        )
        thread_data = [
            {
                "id": 2, "uid": "99", "folder": "Sent",
                "subject": "Test", "sender": "me@test.com",
                "sender_display": "me", "sender_tooltip": "me@test.com",
                "recipients": "them@test.com", "recipients_display": "them",
                "date": "Mon, 1 Jan 2024 09:00:00 +0000", "date_display": "09:00",
                "date_ts": 1704096000, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": True, "is_current": False,
                "snippet": "sent msg", "body_html": "<html>s</html>",
                "has_attachments": False, "cc": "",
            },
            {
                "id": 1, "uid": "100", "folder": "INBOX",
                "subject": "Test", "sender": "s@test.com",
                "sender_display": "s", "sender_tooltip": "s@test.com",
                "recipients": "r@test.com", "recipients_display": "r",
                "date": "Mon, 1 Jan 2024 10:00:00 +0000", "date_display": "10:00",
                "date_ts": 1704099600, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_current": True,
                "snippet": "body", "body_html": "<html>b</html>",
                "has_attachments": False, "cc": "",
            },
        ]
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (mock_msg, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = thread_data
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "bg-indigo-100" in html
        assert "You" in html

    def test_sent_message_shows_cc(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        mock_msg = (
            1, "100", "INBOX", "Test", "s@test.com", "r@test.com",
            "date", '["\\\\Seen"]', "body", "", None, 0, "<msg@test.com>", "t1", "",
        )
        thread_data = [
            {
                "id": 2, "uid": "99", "folder": "Sent",
                "subject": "Test", "sender": "me@test.com",
                "sender_display": "me", "sender_tooltip": "me@test.com",
                "recipients": "them@test.com", "recipients_display": "them",
                "recipients_email": "them@test.com",
                "date": "Mon, 1 Jan 2024 09:00:00 +0000", "date_display": "09:00",
                "date_ts": 1704096000, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": True, "is_current": False,
                "snippet": "sent msg", "body_html": "<html>s</html>",
                "has_attachments": False, "cc": "cc@test.com",
            },
            {
                "id": 1, "uid": "100", "folder": "INBOX",
                "subject": "Test", "sender": "s@test.com",
                "sender_display": "s", "sender_tooltip": "s@test.com",
                "recipients": "r@test.com", "recipients_display": "r",
                "date": "Mon, 1 Jan 2024 10:00:00 +0000", "date_display": "10:00",
                "date_ts": 1704099600, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_current": True,
                "snippet": "body", "body_html": "<html>b</html>",
                "has_attachments": False, "cc": "",
            },
        ]
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (mock_msg, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = thread_data
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "cc cc@test.com" in html

    def test_draft_message_shows_cc(self, app, authed_client):
        client, user_id, account_id = authed_client
        url = f"/app/mail/message/{account_id}/1"
        mock_msg = (
            1, "100", "INBOX", "Test", "s@test.com", "r@test.com",
            "date", '["\\\\Seen"]', "body", "", None, 0, "<msg@test.com>", "t1", "",
        )
        thread_data = [
            {
                "id": 2, "uid": "99", "folder": "Drafts",
                "subject": "Test", "sender": "me@test.com",
                "sender_display": "me", "sender_tooltip": "me@test.com",
                "recipients": "them@test.com", "recipients_display": "them",
                "recipients_email": "them@test.com",
                "date": "Mon, 1 Jan 2024 09:00:00 +0000", "date_display": "09:00",
                "date_ts": 1704096000, "flags": ["\\Draft"],
                "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_draft": True, "is_current": False,
                "snippet": "draft msg", "body_html": "<html>d</html>",
                "has_attachments": False, "cc": "draft-cc@test.com",
            },
            {
                "id": 1, "uid": "100", "folder": "INBOX",
                "subject": "Test", "sender": "s@test.com",
                "sender_display": "s", "sender_tooltip": "s@test.com",
                "recipients": "r@test.com", "recipients_display": "r",
                "date": "Mon, 1 Jan 2024 10:00:00 +0000", "date_display": "10:00",
                "date_ts": 1704099600, "flags": ["\\Seen"],
                "is_unread": False, "is_flagged": False,
                "is_sent": False, "is_current": True,
                "snippet": "body", "body_html": "<html>b</html>",
                "has_attachments": False, "cc": "",
            },
        ]
        with patch("app.modules.mail.controllers.message._load_message_detail") as mock_load, \
             patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings, \
             patch("app.modules.mail.controllers.message.open_cache") as mock_cache, \
             patch("app.modules.mail.controllers.message.list_cached_folders") as mock_folders, \
             patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam, \
             patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread:
            mock_load.return_value = (mock_msg, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = thread_data
            resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "cc draft-cc@test.com" in html


class TestWrapEmailHtmlQuotedCss:
    def test_quoted_css_present(self):
        html = wrap_email_html("<p>test</p>")
        assert ".lr-quoted" in html
        assert ".lr-quoted-toggle" in html

    def test_collapsed_quotes_render_in_iframe(self):
        body = "<p>Hello</p><blockquote><p>Quoted</p></blockquote>"
        collapsed = add_quoted_collapse(body)
        wrapped = wrap_email_html(collapsed)
        assert "<details" in wrapped
        assert "<blockquote" in wrapped


_UNSET = object()


def _make_thread_db(tmp_path):
    from app.modules.mail.services.cache_db import init_cache_schema
    import sqlcipher3

    db_path = str(tmp_path / "test.db")
    conn = sqlcipher3.connect(db_path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{'0' * 64}'\"")
    init_cache_schema(conn)
    return conn


def _insert(conn, uid, folder, subject, sender, recipients, date, thread_id=None, message_id=_UNSET):
    from app.modules.mail.services.cache_db import upsert_message

    if message_id is _UNSET:
        message_id = f"<msg{uid}@test.com>"
    upsert_message(
        conn, uid, folder, subject, sender, recipients,
        date, [], f"snippet-{uid}", f"body-{uid}", False,
        message_id, thread_id=thread_id,
    )


class TestBuildThreads:
    def test_same_thread_id_grouped(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        _insert(conn, "1", "INBOX", "Re: Test", "a@b.com", "c@d.com",
                "Mon, 1 Jan 2024 10:00:00 +0000", thread_id="thread-abc")
        _insert(conn, "2", "INBOX", "Re: Test", "c@d.com", "a@b.com",
                "Mon, 1 Jan 2024 11:00:00 +0000", thread_id="thread-abc")

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 2
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 2

    def test_subject_fallback_no_thread_id(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        _insert(conn, "1", "INBOX", "Re: Hello", "a@b.com", "c@d.com",
                "Mon, 1 Jan 2024 10:00:00 +0000", thread_id=None, message_id=None)
        _insert(conn, "2", "INBOX", "Re: Hello", "c@d.com", "a@b.com",
                "Mon, 1 Jan 2024 11:00:00 +0000", thread_id=None, message_id=None)

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 2
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 2

    def test_different_thread_ids_same_subject_merged(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        _insert(conn, "1", "INBOX", "Re: Project discussion", "a@b.com", "c@d.com",
                "Mon, 1 Jan 2024 10:00:00 +0000", thread_id="thread-alpha")
        _insert(conn, "2", "INBOX", "Re: Project discussion", "c@d.com", "a@b.com",
                "Mon, 1 Jan 2024 11:00:00 +0000", thread_id="thread-beta")
        _insert(conn, "3", "INBOX", "Re: Project discussion", "d@e.com", "a@b.com",
                "Mon, 1 Jan 2024 12:00:00 +0000", thread_id="thread-alpha")
        _insert(conn, "4", "INBOX", "Re: Project discussion", "e@f.com", "a@b.com",
                "Mon, 1 Jan 2024 13:00:00 +0000", thread_id="thread-beta")

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 4
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 4

    def test_mixed_thread_ids_and_null(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        _insert(conn, "1", "INBOX", "Re: Big thread", "a@b.com", "c@d.com",
                "Mon, 1 Jan 2024 10:00:00 +0000", thread_id="thread-1")
        _insert(conn, "2", "INBOX", "Re: Big thread", "c@d.com", "a@b.com",
                "Mon, 1 Jan 2024 11:00:00 +0000", thread_id="thread-2")
        _insert(conn, "3", "INBOX", "Re: Big thread", "d@e.com", "a@b.com",
                "Mon, 1 Jan 2024 12:00:00 +0000", thread_id=None)

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 3
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 3

    def test_different_subjects_not_merged(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        _insert(conn, "1", "INBOX", "Re: Alpha", "a@b.com", "c@d.com",
                "Mon, 1 Jan 2024 10:00:00 +0000", thread_id="thread-x")
        _insert(conn, "2", "INBOX", "Re: Beta", "a@b.com", "c@d.com",
                "Mon, 1 Jan 2024 11:00:00 +0000", thread_id="thread-y")

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 2
        assert len(threads) == 2

    def test_sent_messages_merged_into_thread(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        _insert(conn, "1", "INBOX", "Re: Test", "a@b.com", "me@test.com",
                "Mon, 1 Jan 2024 10:00:00 +0000", thread_id="thread-abc")
        _insert(conn, "2", "Sent", "Re: Test", "me@test.com", "a@b.com",
                "Mon, 1 Jan 2024 11:00:00 +0000", thread_id="thread-abc")

        threads, pagination = _build_threads(conn, "INBOX", account_email="me@test.com")
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 2
        assert any(r.get("is_sent") for r in group)

    def test_sent_merged_across_different_thread_ids(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        _insert(conn, "1", "INBOX", "Re: Proposal", "a@b.com", "me@test.com",
                "Mon, 1 Jan 2024 10:00:00 +0000", thread_id="thread-a")
        _insert(conn, "2", "INBOX", "Re: Proposal", "c@d.com", "me@test.com",
                "Mon, 1 Jan 2024 11:00:00 +0000", thread_id="thread-b")
        _insert(conn, "3", "Sent", "Re: Proposal", "me@test.com", "a@b.com",
                "Mon, 1 Jan 2024 12:00:00 +0000", thread_id="thread-a")

        threads, pagination = _build_threads(conn, "INBOX", account_email="me@test.com")
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 3

    def test_many_messages_cross_thread_ids_triggers_collapse(self, tmp_path):
        from app.modules.mail.controllers.helpers import _build_threads

        conn = _make_thread_db(tmp_path)
        for i in range(11):
            tid = f"thread-{i % 3}"
            _insert(conn, str(i + 1), "INBOX", "Re: Intro via Austen",
                    f"s{i}@test.com", "r@test.com",
                    f"Mon, {i + 1} Jan 2024 10:00:00 +0000", thread_id=tid)

        threads, pagination = _build_threads(conn, "INBOX")
        assert pagination["total_messages"] == 11
        assert len(threads) == 1
        group = list(threads.values())[0]
        assert len(group) == 11


class TestFolderThreadCollapse:
    def _make_msg(self, idx, is_sent=False):
        return {
            "id": idx, "subject": f"Re: Thread test",
            "sender": "sender@test.com",
            "sender_display": "Sender",
            "sender_tooltip": "sender@test.com",
            "snippet": f"snippet {idx}",
            "date": "Mon, 1 Jan 2024 10:00:00 +0000",
            "date_ts": 1704096000 + idx,
            "sort_ts": 1704096000 + idx,
            "date_display": "10:00",
            "flags": ["\\Seen"],
            "is_unread": False,
            "is_flagged": False,
            "folder": "Sent" if is_sent else "INBOX",
            "thread_id": "thread-1",
            "is_sent": is_sent,
            "recipients_display": "recipient@test.com",
        }

    def test_five_messages_shows_collapse_bar(self, app, authed_client):
        client, user_id, account_id = authed_client
        msgs = {f"Re: Thread test": [self._make_msg(i) for i in range(1, 6)]}
        with (
            patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
            patch("app.modules.mail.controllers.mailbox._build_threads", return_value=(msgs, {"total_threads": 1, "total_messages": 5, "current_page": 1, "total_pages": 1, "per_page": 50})),
            patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=MagicMock()),
            patch("app.modules.mail.controllers.mailbox._snippet_debug_enabled", return_value=False),
            patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
        ):
            resp = client.get(f"/app/mail/folder/{account_id}/INBOX/messages")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        html = data["html"]
        assert "data-thread-collapse-toggle" in html
        assert "3 older message" in html
        assert "thread-count" in html

    def test_four_messages_no_collapse_bar(self, app, authed_client):
        client, user_id, account_id = authed_client
        msgs = {f"Re: Thread test": [self._make_msg(i) for i in range(1, 5)]}
        with (
            patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
            patch("app.modules.mail.controllers.mailbox._build_threads", return_value=(msgs, {"total_threads": 1, "total_messages": 4, "current_page": 1, "total_pages": 1, "per_page": 50})),
            patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=MagicMock()),
            patch("app.modules.mail.controllers.mailbox._snippet_debug_enabled", return_value=False),
            patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
        ):
            resp = client.get(f"/app/mail/folder/{account_id}/INBOX/messages")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        html = data["html"]
        assert "data-thread-collapse-toggle" not in html
        assert "thread-count" in html

    def test_eleven_messages_collapse_bar_count(self, app, authed_client):
        client, user_id, account_id = authed_client
        msgs = {f"Re: Thread test": [self._make_msg(i) for i in range(1, 12)]}
        with (
            patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
            patch("app.modules.mail.controllers.mailbox._build_threads", return_value=(msgs, {"total_threads": 1, "total_messages": 11, "current_page": 1, "total_pages": 1, "per_page": 50})),
            patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=MagicMock()),
            patch("app.modules.mail.controllers.mailbox._snippet_debug_enabled", return_value=False),
            patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
        ):
            resp = client.get(f"/app/mail/folder/{account_id}/INBOX/messages")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        html = data["html"]
        assert "data-thread-collapse-toggle" in html
        assert "9 older message" in html
        assert "thread-count" in html
