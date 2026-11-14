"""MCP draft/send tool tests: honest mutations, unified IDs, threading,
flattened envelope (U6.5b/c, U15.26, U16.17 — issues #1-#7 from the
2026-11-14 session)."""

import asyncio
import json
from unittest.mock import MagicMock, patch

from tests.mcp.test_mcp_mail import mcp_mail  # noqa: F401 — fixture reuse

MAIL = "app.mcp.tools.mail"
IMAP = "app.modules.mail.services.imap_client"


def _imap_mock(append_uid="42", search_results=None):
    mock = MagicMock()
    mock.append.return_value = ("OK", [f"[APPENDUID 1 {append_uid}]".encode()])
    mock.select.return_value = ("OK", [b"1"])
    searches = search_results or {}

    def _uid(cmd, *args):
        if cmd == "SEARCH":
            crit = args[-1] if args else ""
            hits = searches.get(crit, b"")
            if isinstance(hits, list):
                return ("OK", [hits.pop(0)])
            return ("OK", [hits])
        return ("OK", [b""])

    mock.uid.side_effect = _uid
    return mock


def _imap_patches(mock):
    return (
        patch(f"{IMAP}.connect_imap", return_value=mock),
        patch(f"{IMAP}.login_imap"),
        patch(f"{IMAP}.fetch_message_with_flags", return_value=(None, [], None)),
    )


class TestMailSaveDraftTool:
    def test_save_draft_returns_flat_envelope(self, mcp_mail):  # noqa: F811
        tools = mcp_mail["tools"]
        mock = _imap_mock(append_uid="344")
        imap_patches = _imap_patches(mock)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
        ):
            result = asyncio.run(
                tools["mail_save_draft"].fn(
                    to=["gemma@example.com"],
                    subject="Re: NEW ROLE",
                    body_plain="reply text",
                )
            )
        data = json.loads(result)
        # U16.17: flattened — no .data envelope
        assert "data" not in data
        assert data["status"] == "draft"
        assert data["draft_uid"] == "344"
        assert data["message_id"]
        # U6.4: stored read
        assert mock.append.call_args[0][1] == "(\\Draft \\Seen)"

    def test_save_draft_replace_by_cache_id(self, mcp_mail):  # noqa: F811
        tools = mcp_mail["tools"]
        # seed a draft row: cache id 7, IMAP uid 343
        conn = MagicMock()
        row = {
            "id": 7,
            "uid": "343",
            "folder": "Drafts",
            "subject": "old",
            "sender": "a",
            "recipients": "b",
            "date": "",
            "flags": '["\\\\Draft", "\\\\Seen"]',
            "snippet": "",
            "body": "",
            "body_html": "",
            "has_attachments": 0,
            "message_id": "<old@example.com>",
            "thread_id": None,
            "cc": "",
        }

        def _execute(sql, params=()):
            m = MagicMock()
            if "SELECT DISTINCT folder" in sql:
                m.fetchall.return_value = [{"folder": "Drafts"}]
                m.fetchone.return_value = None
            elif "WHERE id = ?" in sql:
                m.fetchone.return_value = row
            else:
                m.fetchone.return_value = row
            return m

        conn.execute.side_effect = _execute
        mock = _imap_mock(append_uid="345", search_results={"UID 343": [b""]})
        imap_patches = _imap_patches(mock)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
            patch(f"{MAIL}._get_cache_conn", return_value=conn),
            patch(f"{IMAP}.delete_message_by_uid") as mock_del,
        ):
            result = asyncio.run(
                tools["mail_save_draft"].fn(
                    to=["gemma@example.com"],
                    subject="Re: NEW ROLE",
                    body_plain="v2",
                    draft_id="7",  # the CACHE id exposed by read tools
                )
            )
        data = json.loads(result)
        assert data["status"] == "draft"
        # resolved to the IMAP uid before deletion (U6.5b) — issue #1 repro
        mock_del.assert_called_once_with(mock, "343")

    def test_save_draft_replace_unknown_returns_draft_not_found(self, mcp_mail):  # noqa: F811
        tools = mcp_mail["tools"]
        conn = MagicMock()

        def _execute(sql, params=()):
            m = MagicMock()
            if "SELECT DISTINCT folder" in sql:
                m.fetchall.return_value = [{"folder": "Drafts"}]
            m.fetchone.return_value = None
            return m

        conn.execute.side_effect = _execute
        mock = _imap_mock(search_results={"UID 1194": [b""]})
        imap_patches = _imap_patches(mock)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
            patch(f"{MAIL}._get_cache_conn", return_value=conn),
        ):
            result = asyncio.run(
                tools["mail_save_draft"].fn(
                    to=["gemma@example.com"],
                    subject="Re: NEW ROLE",
                    body_plain="reply",
                    draft_id="1194",
                )
            )
        data = json.loads(result)
        assert data["error"]["code"] == "DRAFT_NOT_FOUND"
        mock.append.assert_not_called()


class TestMailDeleteDraftTool:
    def test_delete_unknown_uid_returns_error(self, mcp_mail):  # noqa: F811
        tools = mcp_mail["tools"]
        conn = MagicMock()

        def _execute(sql, params=()):
            m = MagicMock()
            if "SELECT DISTINCT folder" in sql:
                m.fetchall.return_value = [{"folder": "Drafts"}]
            m.fetchone.return_value = None
            return m

        conn.execute.side_effect = _execute
        mock = _imap_mock(search_results={"UID 343": [b""]})
        imap_patches = _imap_patches(mock)
        with (
            imap_patches[0],
            imap_patches[1],
            imap_patches[2],
            patch(f"{MAIL}._get_cache_conn", return_value=conn),
        ):
            result = asyncio.run(tools["mail_delete_draft"].fn(draft_uid="343"))
        data = json.loads(result)
        # issue #2a repro: never a phantom success
        assert data["error"]["code"] == "DRAFT_NOT_FOUND"
        assert "deleted" not in json.dumps(data.get("status", ""))


class TestMailSendTool:
    def _smtp_patches(self):
        mock_smtp = MagicMock()
        mock_send = MagicMock()
        return (
            mock_send,
            patch(
                "app.modules.mail.services.smtp_client.smtp_connect",
                return_value=mock_smtp,
            ),
            patch("app.modules.mail.services.smtp_client.smtp_login"),
            patch("app.modules.mail.services.smtp_client.smtp_send", mock_send),
        )

    def test_send_threads_with_in_reply_to(self, mcp_mail):  # noqa: F811
        import email as _email

        tools = mcp_mail["tools"]
        mock_send, *smtp_patches = self._smtp_patches()
        mock = _imap_mock()
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch("app.modules.mail.services.send._imap_connect", return_value=mock),
        ):
            result = asyncio.run(
                tools["mail_send"].fn(
                    to=["gemma@example.com"],
                    subject="Re: NEW ROLE",
                    body_plain="reply",
                    in_reply_to="<179248033325.8@example.com>",
                )
            )
        data = json.loads(result)
        assert data["status"] == "sent"
        msg = _email.message_from_bytes(mock_send.call_args[0][3])
        assert msg["In-Reply-To"] == "<179248033325.8@example.com>"

    def test_send_reports_draft_deleted(self, mcp_mail):  # noqa: F811
        tools = mcp_mail["tools"]
        row = {
            "id": 9,
            "uid": "345",
            "folder": "Drafts",
            "flags": '["\\\\Draft"]',
        }
        conn = MagicMock()

        def _execute(sql, params=()):
            m = MagicMock()
            if "SELECT DISTINCT folder" in sql:
                m.fetchall.return_value = [{"folder": "Drafts"}]
            m.fetchone.return_value = row
            return m

        conn.execute.side_effect = _execute
        _mock_send, *smtp_patches = self._smtp_patches()
        mock = _imap_mock()
        with (
            smtp_patches[0],
            smtp_patches[1],
            smtp_patches[2],
            patch("app.modules.mail.services.send._imap_connect", return_value=mock),
            patch(f"{MAIL}._get_cache_conn", return_value=conn),
            patch(f"{IMAP}.delete_message_by_uid"),
        ):
            result = asyncio.run(
                tools["mail_send"].fn(
                    to=["gemma@example.com"],
                    subject="Re: NEW ROLE",
                    body_plain="reply",
                    draft_id="345",
                )
            )
        data = json.loads(result)
        assert data["status"] == "sent"
        assert data["draft_deleted"] is True


class TestMcpAiActivityEmission:
    """U27.2/U27.4: MCP tool calls emit ai_activity via resilient_tool +
    the events bridge (customer/account from the tool-call context)."""

    def test_tool_call_emits_ai_activity_local_bridge(self, mcp_mail, monkeypatch):  # noqa: F811
        import asyncio

        from app.mcp import events_bridge
        from app.shared import events

        monkeypatch.setattr(events_bridge, "_BRIDGE_MODE", "local")
        captured: list = []

        def _rec(user_id, event_type, data):
            if event_type == "ai_activity":
                captured.append((user_id, data))

        events._subscribers.append(_rec)
        try:
            tools = mcp_mail["tools"]
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = []
            mock = MagicMock()
            mock.select.return_value = ("OK", [b"1"])
            with (
                patch(f"{MAIL}._get_cache_conn", return_value=conn),
                patch(
                    "app.modules.mail.services.imap_client.connect_imap",
                    return_value=mock,
                ),
                patch("app.modules.mail.services.imap_client.login_imap"),
            ):
                result = asyncio.run(tools["mail_list_folders"].fn())
            assert json.loads(result)["items"] == []
        finally:
            events._subscribers.remove(_rec)
        assert captured, "ai_activity event not emitted for MCP tool call"
        user_id, data = captured[-1]
        assert user_id == mcp_mail["user_id"]
        assert data["tool"] == "mail_list_folders"
        assert data["status"] == "ok"
        assert isinstance(data["duration_ms"], int)
        assert data["account_id"] == mcp_mail["account_id"]

    def test_failed_tool_call_emits_error_status(self, mcp_mail, monkeypatch):  # noqa: F811
        import asyncio

        from app.mcp import events_bridge
        from app.shared import events

        monkeypatch.setattr(events_bridge, "_BRIDGE_MODE", "local")
        captured: list = []

        def _rec(user_id, event_type, data):
            if event_type == "ai_activity":
                captured.append(data)

        events._subscribers.append(_rec)
        try:
            tools = mcp_mail["tools"]
            with patch(
                f"{MAIL}._get_cache_conn",
                side_effect=RuntimeError("boom"),
            ):
                result = asyncio.run(tools["mail_list_folders"].fn())
            body = json.loads(result)
            assert "error" in body
        finally:
            events._subscribers.remove(_rec)
        assert captured
        assert captured[-1]["status"].startswith("error")


class TestMailGetThreadTool:
    def test_get_thread_body_options(self, mcp_mail):  # noqa: F811
        tools = mcp_mail["tools"]
        row = MagicMock()
        vals = {
            "id": 1,
            "uid": "100",
            "folder": "INBOX",
            "subject": "s",
            "sender": "a",
            "recipients": "b",
            "cc": "",
            "date": "",
            "flags": '["\\\\Seen"]',
            "snippet": "snip",
            "body": "plain body",
            "body_html": "<p>html</p>",
            "thread_id": "t1",
        }
        row.__getitem__ = lambda self, k: vals[k]
        row.keys = lambda: list(vals.keys())
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [row]
        with patch(f"{MAIL}._get_cache_conn", return_value=conn):
            plain_only = asyncio.run(
                tools["mail_get_thread"].fn(thread_id="t1", body_format="plain")
            )
            no_bodies = asyncio.run(
                tools["mail_get_thread"].fn(thread_id="t1", include_bodies=False)
            )
        plain_items = json.loads(plain_only)["items"]
        assert plain_items[0]["body_plain"] == "plain body"
        assert "body_html" not in plain_items[0]
        header_items = json.loads(no_bodies)["items"]
        assert "body_plain" not in header_items[0]
        assert header_items[0]["snippet"] == "snip"

    def test_message_summary_exposes_imap_uid(self, mcp_mail):  # noqa: F811
        tools = mcp_mail["tools"]
        row = MagicMock()
        vals = {
            "id": 5,
            "uid": "343",
            "folder": "Drafts",
            "subject": "s",
            "sender": "a",
            "recipients": "b",
            "cc": "",
            "date": "",
            "flags": '["\\\\Draft", "\\\\Seen"]',
            "snippet": "snip",
            "thread_id": None,
        }
        row.__getitem__ = lambda self, k: vals[k]
        row.keys = lambda: list(vals.keys())
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row
        with patch(f"{MAIL}._get_cache_conn", return_value=conn):
            result = asyncio.run(tools["mail_get_message"].fn(message_id=5))
        data = json.loads(result)
        assert data["imap_uid"] == "343"
        assert data["folder"] == "Drafts"
