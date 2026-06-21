from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from app.api.token_service import create_api_token, generate_dek
from app.shared.db import db as _db
from app.shared.keys import clear_user_key, set_user_key
from app.shared.models.core import CustomerAccount, Domain, User

MAIL = "app.mcp.tools.mail"
CACHE_DB = "app.modules.mail.services.cache_db"
IMAP_CLIENT = "app.modules.mail.services.imap_client"
UI_EVENTS = "app.shared.ui_events"


@pytest.fixture()
def mcp_mail(app, _clean_db):
    user_id = None
    account_id = None

    with app.app_context():
        user = User(email="mcp-mail@example.com", role="customer", is_active=True)  # type: ignore[call-arg]
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain(  # type: ignore[call-arg]
            name="example.com",
            is_active=True,
            status="active",
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        _db.session.add(domain)
        _db.session.flush()

        dek = generate_dek()
        account = CustomerAccount(  # type: ignore[call-arg]
            customer_id=user.id,
            domain_id=domain.id,
            email_address="mcp-mail@example.com",
            auth_type="password",
            username="mcp-mail@example.com",
            cache_db_path="",
            api_enabled=True,
            dek_wrapped_cred=b"placeholder",
            is_active=True,
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    token_value = None
    with app.app_context():
        token_value, _ = create_api_token(user_id, dek, "test-token", ["mail:read", "mail:write"])

    from app.mcp.auth import set_current_token

    set_current_token(token_value)

    mcp = FastMCP("test")
    from app.mcp.tools.mail import register as register_mail

    register_mail(mcp, app)

    tools = mcp._tool_manager._tools

    yield {
        "app": app,
        "tools": tools,
        "user_id": user_id,
        "account_id": account_id,
    }

    set_current_token("")
    clear_user_key(user_id)


def _mock_conn():
    conn = MagicMock()
    conn.close = MagicMock()
    return conn


def _mock_folder_row(name, unread_count):
    r = MagicMock()
    vals = {"name": name, "unread_count": unread_count}
    r.__getitem__ = lambda self, k: vals[k]
    r.keys = lambda: list(vals.keys())
    return r


def _mock_message_row(
    msg_id,
    subject,
    sender="test@example.com",
    folder="INBOX",
    flags='["\\Seen"]',
    snippet="",
    thread_id=None,
    recipients="",
    cc="",
    date="",
    uid="1",
):
    keys = [
        "id",
        "subject",
        "sender",
        "folder",
        "flags",
        "snippet",
        "thread_id",
        "recipients",
        "cc",
        "date",
        "uid",
    ]
    vals = {
        "id": msg_id,
        "subject": subject,
        "sender": sender,
        "folder": folder,
        "flags": flags,
        "snippet": snippet,
        "thread_id": thread_id,
        "recipients": recipients,
        "cc": cc,
        "date": date,
        "uid": uid,
    }
    r = MagicMock()
    r.__getitem__ = lambda self, k: vals[k]
    r.keys = lambda: keys
    return r


class TestMcpMailListTools:
    def test_list_folders(self, mcp_mail):
        tools = mcp_mail["tools"]
        mock_row = _mock_folder_row("INBOX", 3)
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.list_cached_folders", return_value=[mock_row]):
                result = asyncio.run(tools["mail_list_folders"].fn())
        data = json.loads(result)["data"]
        assert data[0]["name"] == "INBOX"

    def test_list_folders_empty(self, mcp_mail):
        tools = mcp_mail["tools"]
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.list_cached_folders", return_value=[]):
                result = asyncio.run(tools["mail_list_folders"].fn())
        data = json.loads(result)["data"]
        assert data == []

    def test_list_folders_with_account_id(self, mcp_mail):
        tools = mcp_mail["tools"]
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.list_cached_folders", return_value=[]):
                result = asyncio.run(
                    tools["mail_list_folders"].fn(account_id=mcp_mail["account_id"])
                )
        data = json.loads(result)["data"]
        assert data == []

    def test_list_messages(self, mcp_mail):
        tools = mcp_mail["tools"]
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.list_messages_with_threading", return_value=[]):
                result = asyncio.run(tools["mail_list_messages"].fn(folder_id="INBOX"))
        data = json.loads(result)
        assert "data" in data

    def test_get_message(self, mcp_mail):
        tools = mcp_mail["tools"]
        mock_row = _mock_message_row(1, "Test Subject")
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=mock_row):
                result = asyncio.run(tools["mail_get_message"].fn(message_id=1))
        data = json.loads(result)["data"]
        assert data["id"] == 1

    def test_get_message_not_found(self, mcp_mail):
        tools = mcp_mail["tools"]
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=None):
                result = asyncio.run(tools["mail_get_message"].fn(message_id=999))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_search(self, mcp_mail):
        tools = mcp_mail["tools"]
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.search_local", return_value=[]):
                result = asyncio.run(tools["mail_search"].fn(q="hello"))
        data = json.loads(result)["data"]
        assert data == []

    def test_get_thread(self, mcp_mail):
        tools = mcp_mail["tools"]
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.list_thread_messages", return_value=[]):
                result = asyncio.run(tools["mail_get_thread"].fn(thread_id="thread-123"))
        data = json.loads(result)["data"]
        assert data == []

    def test_list_messages_includes_protected(self, mcp_mail):
        # HLD U5.15h: the protected state is visible before a delete is attempted.
        tools = mcp_mail["tools"]
        flagged_row = _mock_message_row(1, "Starred", flags='["\\\\Flagged"]')
        plain_row = _mock_message_row(2, "Plain", flags='["\\\\Seen"]')
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(
                f"{CACHE_DB}.list_messages_with_threading", return_value=[flagged_row, plain_row]
            ):
                result = asyncio.run(tools["mail_list_messages"].fn(folder_id="INBOX"))
        by_id = {m["id"]: m for m in json.loads(result)["data"]}
        assert by_id[1]["protected"] is True
        assert by_id[2]["protected"] is False


class TestMcpMailMutationTools:
    def test_update_flags(self, mcp_mail):
        tools = mcp_mail["tools"]
        mock_row = _mock_message_row(1, "Test")
        mock_account = MagicMock()
        mock_domain = MagicMock()
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=mock_row):
                with patch(f"{CACHE_DB}.update_flags"):
                    with patch(
                        f"{MAIL}._get_account_and_secret",
                        return_value=(mock_account, mock_domain, "pass"),
                    ):
                        with patch(f"{MAIL}._imap_connect"):
                            with patch(f"{UI_EVENTS}.push_ui_event"):
                                result = asyncio.run(
                                    tools["mail_update_flags"].fn(message_id=1, read=True)
                                )
        data = json.loads(result)["data"]
        assert data["id"] == 1

    def test_bulk_move_validates_items(self, mcp_mail):
        tools = mcp_mail["tools"]
        result = asyncio.run(tools["mail_bulk_move"].fn(items=[], folder_id="Archive"))
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_bulk_flag_validates_items(self, mcp_mail):
        tools = mcp_mail["tools"]
        result = asyncio.run(tools["mail_bulk_flag"].fn(items=[]))
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_bulk_delete_validates_items(self, mcp_mail):
        tools = mcp_mail["tools"]
        result = asyncio.run(tools["mail_bulk_delete"].fn(items=[]))
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_delete_message_refuses_starred(self, mcp_mail):
        # Previously untested MCP protection path (mail_delete_message).
        tools = mcp_mail["tools"]
        # JSON-encoded flags: the literal text must be ["\\Flagged"] so json.loads
        # yields the list ["\Flagged"] (the real IMAP flag).
        starred_row = _mock_message_row(1, "Starred", flags='["\\\\Flagged"]')
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=starred_row):
                result = asyncio.run(tools["mail_delete_message"].fn(message_id=1))
        data = json.loads(result)
        assert data["error"]["code"] == "PROTECTED"
        assert "starred" in data["error"]["message"].lower()
        assert "unstar" in data["error"]["message"].lower()

    def test_delete_message_refuses_locked(self, mcp_mail):
        tools = mcp_mail["tools"]
        locked_row = _mock_message_row(1, "Locked", flags='["$Locked"]')
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=locked_row):
                result = asyncio.run(tools["mail_delete_message"].fn(message_id=1))
        data = json.loads(result)
        assert data["error"]["code"] == "PROTECTED"
        assert "locked" in data["error"]["message"].lower()
        assert "unlock" in data["error"]["message"].lower()

    def test_bulk_delete_skips_protected(self, mcp_mail):
        # Previously untested MCP bulk protection path (mail_bulk_delete).
        tools = mcp_mail["tools"]
        starred_row = _mock_message_row(1, "Starred", flags='["\\\\Flagged"]')
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=starred_row):
                result = asyncio.run(tools["mail_bulk_delete"].fn(items=[{"message_id": 1}]))
        data = json.loads(result)["data"]
        codes = [f["error"]["code"] for f in data["failed"]]
        assert "PROTECTED" in codes
        assert data["succeeded"] == []

    def test_move_to_trash_refuses_starred(self, mcp_mail):
        # MCP move-to-Trash must enforce protection (parity with delete).
        tools = mcp_mail["tools"]
        starred_row = _mock_message_row(1, "Starred", flags='["\\\\Flagged"]')
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=starred_row):
                result = asyncio.run(tools["mail_move_message"].fn(message_id=1, folder_id="Trash"))
        data = json.loads(result)
        assert data["error"]["code"] == "PROTECTED"
        assert "starred" in data["error"]["message"].lower()

    def test_move_to_trash_refuses_locked(self, mcp_mail):
        tools = mcp_mail["tools"]
        locked_row = _mock_message_row(1, "Locked", flags='["$Locked"]')
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=locked_row):
                result = asyncio.run(tools["mail_move_message"].fn(message_id=1, folder_id="Trash"))
        data = json.loads(result)
        assert data["error"]["code"] == "PROTECTED"
        assert "locked" in data["error"]["message"].lower()

    def test_move_to_non_trash_allows_protected(self, mcp_mail):
        # Protection blocks only Trash moves; archiving a starred message is fine.
        tools = mcp_mail["tools"]
        starred_row = _mock_message_row(1, "Starred", flags='["\\\\Flagged"]')
        mock_account = MagicMock()
        mock_domain = MagicMock()
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=starred_row):
                with patch(
                    f"{MAIL}._get_account_and_secret",
                    return_value=(mock_account, mock_domain, "pass"),
                ):
                    with patch(f"{MAIL}._imap_connect"):
                        with patch(f"{IMAP_CLIENT}.select_folder"):
                            with patch(f"{IMAP_CLIENT}.move_message"):
                                with patch(f"{IMAP_CLIENT}.safe_logout"):
                                    with patch(f"{UI_EVENTS}.push_ui_event"):
                                        result = asyncio.run(
                                            tools["mail_move_message"].fn(
                                                message_id=1, folder_id="Archive"
                                            )
                                        )
        data = json.loads(result)["data"]
        assert data["moved_to"] == "Archive"

    def test_bulk_move_to_trash_skips_protected(self, mcp_mail):
        # MCP bulk move-to-Trash must skip protected messages (parity with bulk delete).
        tools = mcp_mail["tools"]
        starred_row = _mock_message_row(1, "Starred", flags='["\\\\Flagged"]')
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=starred_row):
                result = asyncio.run(
                    tools["mail_bulk_move"].fn(items=[{"message_id": 1}], folder_id="Trash")
                )
        data = json.loads(result)["data"]
        codes = [f["error"]["code"] for f in data["failed"]]
        assert "PROTECTED" in codes
        assert data["succeeded"] == []

    def test_create_folder_enqueues_sync(self, mcp_mail):
        # Parity with REST: folder creation must enqueue a cache sync.
        tools = mcp_mail["tools"]
        app = mcp_mail["app"]
        sync_mock = MagicMock()
        app.sync_manager = sync_mock
        with (
            patch(
                f"{MAIL}._get_account_and_secret", return_value=(MagicMock(), MagicMock(), "pass")
            ),
            patch(f"{MAIL}._imap_connect", return_value=MagicMock()),
            patch(f"{IMAP_CLIENT}.list_folders", return_value=[]),
            patch(f"{IMAP_CLIENT}.get_folder_delimiter", return_value="/"),
            patch(f"{IMAP_CLIENT}.create_folder", return_value=("OK", [b""])),
            patch(f"{IMAP_CLIENT}.encode_mailbox_name", side_effect=lambda x: x),
            patch(f"{IMAP_CLIENT}.safe_logout"),
            patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()),
            patch(f"{CACHE_DB}.upsert_folder"),
            patch(f"{UI_EVENTS}.push_ui_event"),
        ):
            result = asyncio.run(tools["mail_create_folder"].fn(name="NewFolder"))
        data = json.loads(result)["data"]
        assert data["created"] is True
        sync_mock.enqueue_sync.assert_any_call(
            mcp_mail["account_id"], folder="NewFolder", reason="folder_created", priority=5
        )

    def test_rename_folder_enqueues_sync(self, mcp_mail):
        tools = mcp_mail["tools"]
        app = mcp_mail["app"]
        sync_mock = MagicMock()
        app.sync_manager = sync_mock
        with (
            patch(
                f"{MAIL}._get_account_and_secret", return_value=(MagicMock(), MagicMock(), "pass")
            ),
            patch(f"{MAIL}._imap_connect", return_value=MagicMock()),
            patch(f"{IMAP_CLIENT}.rename_folder", return_value=("OK", [b""])),
            patch(f"{IMAP_CLIENT}.encode_mailbox_name", side_effect=lambda x: x),
            patch(f"{IMAP_CLIENT}.safe_logout"),
            patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()),
            patch(f"{CACHE_DB}.rename_folder_in_cache"),
            patch(f"{UI_EVENTS}.push_ui_event"),
        ):
            result = asyncio.run(tools["mail_rename_folder"].fn(folder_id="Old", name="New"))
        data = json.loads(result)["data"]
        assert data["name"] == "New"
        sync_mock.enqueue_sync.assert_any_call(
            mcp_mail["account_id"], folder="New", reason="folder_renamed", priority=5
        )
