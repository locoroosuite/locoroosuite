from __future__ import annotations

import asyncio
import json
from unittest.mock import patch, MagicMock

import pytest

from mcp.server.fastmcp import FastMCP
from app.shared.db import db as _db
from app.shared.models.core import User, Domain, CustomerAccount
from app.shared.keys import set_user_key, clear_user_key
from app.api.token_service import create_api_token, generate_dek


MAIL = "app.mcp.tools.mail"
CACHE_DB = "app.modules.mail.services.cache_db"
IMAP_CLIENT = "app.modules.mail.services.imap_client"
UI_EVENTS = "app.shared.ui_events"


@pytest.fixture()
def mcp_mail(app, _clean_db):
    user_id = None
    account_id = None

    with app.app_context():
        user = User(email="mcp-mail@example.com", role="customer", is_active=True)
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain(
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
        account = CustomerAccount(
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
        token_value, _ = create_api_token(
            user_id, dek, "test-token", ["mail:read", "mail:write"]
        )

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


def _mock_message_row(msg_id, subject, sender="test@example.com", folder="INBOX",
                       flags='["\\Seen"]', snippet="", thread_id=None,
                       recipients="", cc="", date="", uid="1"):
    keys = ["id", "subject", "sender", "folder", "flags", "snippet",
            "thread_id", "recipients", "cc", "date", "uid"]
    vals = {"id": msg_id, "subject": subject, "sender": sender, "folder": folder,
            "flags": flags, "snippet": snippet, "thread_id": thread_id,
            "recipients": recipients, "cc": cc, "date": date, "uid": uid}
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
                result = asyncio.run(tools["mail_list_folders"].fn(account_id=mcp_mail["account_id"]))
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


class TestMcpMailMutationTools:
    def test_update_flags(self, mcp_mail):
        tools = mcp_mail["tools"]
        mock_row = _mock_message_row(1, "Test")
        mock_account = MagicMock()
        mock_domain = MagicMock()
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CACHE_DB}.get_message", return_value=mock_row):
                with patch(f"{CACHE_DB}.update_flags"):
                    with patch(f"{MAIL}._get_account_and_secret", return_value=(mock_account, mock_domain, "pass")):
                        with patch(f"{MAIL}._imap_connect"):
                            with patch(f"{UI_EVENTS}.push_ui_event"):
                                result = asyncio.run(tools["mail_update_flags"].fn(message_id=1, read=True))
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
