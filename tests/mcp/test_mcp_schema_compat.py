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


LIST_TOOLS_WITH_MAX_RESULTS = [
    "contacts_list",
    "contacts_search",
    "mail_list_messages",
    "mail_search",
    "calendar_list_events",
    "calendar_search_events",
    "docs_list_documents",
]

READ_ONLY_TOOLS = [
    "contacts_list",
    "contacts_search",
    "contacts_get",
    "mail_list_folders",
    "mail_list_messages",
    "mail_get_message",
    "mail_search",
    "mail_get_thread",
    "mail_get_attachment",
    "mail_view_attachment",
    "mail_get_raw_message",
    "calendar_list_calendars",
    "calendar_list_events",
    "calendar_get_event",
    "calendar_search_events",
    "calendar_check_free_busy",
    "docs_list_documents",
    "docs_get_document",
    "docs_download_document",
    "docs_read_content",
    "docs_export_pdf",
    "docs_list_drafts",
]


@pytest.fixture()
def mcp_all_tools(app, _clean_db):
    user_id = None
    account_id = None

    with app.app_context():
        user = User(email="mcp-schema@example.com", role="customer", is_active=True)
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
            email_address="mcp-schema@example.com",
            auth_type="password",
            username="mcp-schema@example.com",
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
            user_id, dek, "test-token", [
                "mail:read", "mail:write",
                "contacts:read", "contacts:write",
                "calendar:read", "calendar:write",
                "docs:read", "docs:write",
            ]
        )

    from app.mcp.auth import set_current_token
    set_current_token(token_value)

    mcp = FastMCP("test")
    from app.mcp.tools.contacts import register as register_contacts
    from app.mcp.tools.mail import register as register_mail
    from app.mcp.tools.calendar import register as register_calendar
    from app.mcp.tools.docs import register as register_docs
    register_contacts(mcp, app)
    register_mail(mcp, app)
    register_calendar(mcp, app)
    register_docs(mcp, app)

    tools = mcp._tool_manager._tools

    yield {
        "app": app,
        "tools": tools,
        "user_id": user_id,
        "account_id": account_id,
    }

    set_current_token("")
    clear_user_key(user_id)


class TestSchemaChatGPTCompatibility:
    def test_no_tool_uses_limit_param(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        for name, tool in tools.items():
            schema = tool.parameters
            assert "limit" not in schema.get("properties", {}), (
                f"Tool '{name}' exposes bare 'limit' parameter — use 'max_results' instead"
            )

    def test_list_tools_have_max_results_with_bounds(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        for name in LIST_TOOLS_WITH_MAX_RESULTS:
            schema = tools[name].parameters
            props = schema["properties"]
            assert "max_results" in props, f"Tool '{name}' missing 'max_results' parameter"
            mr = props["max_results"]
            if "anyOf" in mr:
                int_schema = next(s for s in mr["anyOf"] if s.get("type") == "integer")
            else:
                int_schema = mr
            assert int_schema.get("minimum") == 1, f"Tool '{name}' max_results missing minimum=1"
            assert int_schema.get("maximum") == 200, f"Tool '{name}' max_results missing maximum=200"
            assert "description" in mr, f"Tool '{name}' max_results missing description"

    def test_all_params_have_descriptions(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        for name, tool in tools.items():
            schema = tool.parameters
            for param_name, param_schema in schema.get("properties", {}).items():
                assert "description" in param_schema, (
                    f"Tool '{name}' parameter '{param_name}' missing description"
                )

    def test_read_only_tools_have_read_only_description(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        for name in READ_ONLY_TOOLS:
            tool = tools[name]
            assert tool.annotations.readOnlyHint is True, (
                f"Read-only tool '{name}' missing readOnlyHint=True"
            )
            assert tool.annotations.destructiveHint is False, (
                f"Read-only tool '{name}' has destructiveHint=True"
            )

    def test_all_tools_have_titles(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        for name, tool in tools.items():
            assert tool.title, f"Tool '{name}' missing title"

    def test_all_tools_have_descriptions(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        for name, tool in tools.items():
            assert tool.description, f"Tool '{name}' missing description"
            assert len(tool.description) >= 15, (
                f"Tool '{name}' description too short: '{tool.description}'"
            )

    def test_max_results_is_optional(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        for name in LIST_TOOLS_WITH_MAX_RESULTS:
            schema = tools[name].parameters
            required = schema.get("required", [])
            assert "max_results" not in required, (
                f"Tool '{name}' has 'max_results' as required — should be optional"
            )

    def test_destructive_tools_have_hint(self, mcp_all_tools):
        tools = mcp_all_tools["tools"]
        destructive_tools = [
            "contacts_delete", "contacts_bulk_delete",
            "mail_delete_message", "mail_bulk_delete", "mail_delete_draft",
            "calendar_delete_calendar", "calendar_delete_event",
            "docs_delete_document", "docs_discard_draft",
        ]
        for name in destructive_tools:
            tool = tools[name]
            assert tool.annotations.destructiveHint is True, (
                f"Destructive tool '{name}' missing destructiveHint=True"
            )
