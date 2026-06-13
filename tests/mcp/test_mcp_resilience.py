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
from app.mcp.errors import resilient_tool, structured_error, get_registry_snapshot, health_check

MAIL = "app.mcp.tools.mail"
CONTACTS = "app.mcp.tools.contacts"
CALENDAR = "app.mcp.tools.calendar"
DOCS = "app.mcp.tools.docs"

MAIL_CACHE_DB = "app.modules.mail.services.cache_db"
CONTACTS_CACHE_DB = "app.modules.contacts.services.cache_db"
DOCS_CACHE_DB = "app.modules.docs.services.cache_db"


def _mock_conn():
    conn = MagicMock()
    conn.close = MagicMock()
    return conn


@pytest.fixture()
def mcp_all(app, _clean_db):
    user_id = None
    with app.app_context():
        user = User(email="resilience@example.com", role="customer", is_active=True)
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
            carddav_host="localhost",
            carddav_port=5232,
            carddav_use_tls=False,
            caldav_host="localhost",
            caldav_port=5232,
            caldav_use_tls=False,
        )
        _db.session.add(domain)
        _db.session.flush()

        dek = generate_dek()
        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address="resilience@example.com",
            auth_type="password",
            username="resilience@example.com",
            cache_db_path="",
            api_enabled=True,
            dek_wrapped_cred=b"placeholder",
            is_active=True,
        )
        _db.session.add(account)
        _db.session.commit()

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

    mcp = FastMCP("test-resilience")
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
        "mcp": mcp,
        "tools": tools,
        "user_id": user_id,
    }

    set_current_token("")
    clear_user_key(user_id)


class TestEndpointExceptionBoundary:
    def test_tool_exception_returns_structured_error(self, mcp_all):
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError("simulated crash")):
            result = asyncio.run(tools["mail_list_folders"].fn())
            data = json.loads(result)
            assert "error" in data
            assert data["error"]["code"] == "INTERNAL_ERROR"
            assert "simulated crash" not in data["error"]["message"]
            assert "RuntimeError" not in data["error"]["message"]
            assert "request_id" in data["error"]
            assert "details" not in data["error"]

    def test_tool_exception_does_not_remove_from_registry(self, mcp_all):
        tools = mcp_all["tools"]
        tool_names_before = set(tools.keys())
        with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError("boom")):
            asyncio.run(tools["mail_list_folders"].fn())
        tool_names_after = set(tools.keys())
        assert tool_names_before == tool_names_after
        assert "mail_list_folders" in tool_names_after

    def test_contacts_exception_returns_structured_error(self, mcp_all):
        tools = mcp_all["tools"]
        with patch(f"{CONTACTS}._get_cache_conn", side_effect=ValueError("carddav failure")):
            result = asyncio.run(tools["contacts_list"].fn())
            data = json.loads(result)
            assert "error" in data
            assert data["error"]["code"] == "INTERNAL_ERROR"
            assert "request_id" in data["error"]
            assert "details" not in data["error"]

    def test_write_tool_exception_returns_structured_error(self, mcp_all):
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=ConnectionError("smtp down")):
            result = asyncio.run(tools["mail_send"].fn(
                to=["test@example.com"],
                subject="Test",
                body_plain="Hello",
            ))
            data = json.loads(result)
            assert "error" in data
            assert data["error"]["code"] in ("INTERNAL_ERROR", "SERVICE_UNAVAILABLE")
            assert "ConnectionError" not in data["error"]["message"]


class TestRegistryStability:
    def test_registry_immutable_after_multiple_failures(self, mcp_all):
        tools = mcp_all["tools"]
        original_count = len(tools)
        original_names = set(tools.keys())
        for _ in range(5):
            with patch(f"{MAIL}._get_cache_conn", side_effect=Exception("fail")):
                asyncio.run(tools["mail_list_folders"].fn())
            with patch(f"{CONTACTS}._get_cache_conn", side_effect=Exception("fail")):
                asyncio.run(tools["contacts_list"].fn())
        assert len(tools) == original_count
        assert set(tools.keys()) == original_names

    def test_registry_snapshot_matches_tools(self, mcp_all):
        mcp = mcp_all["mcp"]
        snapshot = get_registry_snapshot(mcp)
        assert snapshot["registered_tools"] > 0
        assert "mail_list_folders" in snapshot["tool_names"]
        assert "contacts_list" in snapshot["tool_names"]

    def test_tool_still_works_after_neighbor_failure(self, mcp_all):
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError("neighbor crash")):
            asyncio.run(tools["mail_list_folders"].fn())
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.list_contacts", return_value=[]):
                with patch(f"{CONTACTS_CACHE_DB}.count_contacts", return_value=0):
                    result = asyncio.run(tools["contacts_list"].fn())
                    data = json.loads(result)
                    assert "data" in data

    def test_cross_module_failure_does_not_affect_other_modules(self, mcp_all):
        tools = mcp_all["tools"]
        with patch(f"{CALENDAR}._get_cache_conn", side_effect=RuntimeError("calendar crash")):
            asyncio.run(tools["calendar_create_event"].fn(
                calendar_id=1, summary="Test", start="2025-01-01T10:00:00+00:00", end="2025-01-01T11:00:00+00:00"
            ))
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.list_documents", return_value=[]):
                result = asyncio.run(tools["docs_list_documents"].fn())
                data = json.loads(result)
                assert "data" in data


class TestRepeatedFailures:
    def test_repeated_failures_still_return_structured_errors(self, mcp_all):
        tools = mcp_all["tools"]
        for i in range(10):
            with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError(f"fail {i}")):
                result = asyncio.run(tools["mail_list_folders"].fn())
                data = json.loads(result)
                assert data["error"]["code"] == "INTERNAL_ERROR"
                assert f"fail {i}" not in data["error"]["message"]

    def test_recovery_after_repeated_failures(self, mcp_all):
        tools = mcp_all["tools"]
        for _ in range(5):
            with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError("fail")):
                asyncio.run(tools["mail_list_folders"].fn())
        with patch(f"{MAIL}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{MAIL_CACHE_DB}.list_cached_folders", return_value=[]):
                result = asyncio.run(tools["mail_list_folders"].fn())
                data = json.loads(result)
                assert "data" in data


class TestHealthEndpoint:
    def test_health_check_returns_expected_shape(self, mcp_all):
        mcp = mcp_all["mcp"]
        result = health_check(mcp)
        assert result["healthy"] is True
        assert result["registered_tools"] > 0
        assert result["session_valid"] is True

    def test_health_check_after_failures(self, mcp_all):
        mcp = mcp_all["mcp"]
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError("fail")):
            asyncio.run(tools["mail_list_folders"].fn())
        result = health_check(mcp)
        assert result["healthy"] is True


class TestStructuredErrorSchema:
    def test_error_has_required_fields(self):
        result = json.loads(structured_error("TEST_CODE", "test message", request_id="req-123"))
        assert result["error"]["code"] == "TEST_CODE"
        assert result["error"]["message"] == "test message"
        assert result["error"]["request_id"] == "req-123"

    def test_error_generates_request_id_if_missing(self):
        result = json.loads(structured_error("CODE", "msg"))
        assert "request_id" in result["error"]
        assert len(result["error"]["request_id"]) == 12

    def test_error_has_no_leaked_details(self):
        result = json.loads(structured_error("CODE", "msg", request_id="abc"))
        assert "details" not in result["error"]
        assert "exception_type" not in result["error"]
        assert "tool" not in result["error"]


class TestServerRestartSimulation:
    def test_fresh_mcp_instance_registers_all_tools(self, app, _clean_db):
        mcp = FastMCP("test-restart")
        from app.mcp.tools.contacts import register as register_contacts
        from app.mcp.tools.mail import register as register_mail
        from app.mcp.tools.calendar import register as register_calendar
        from app.mcp.tools.docs import register as register_docs
        register_contacts(mcp, app)
        register_mail(mcp, app)
        register_calendar(mcp, app)
        register_docs(mcp, app)
        tools = mcp._tool_manager._tools
        assert len(tools) > 0

    def test_two_separate_mcp_instances_independent(self, app, _clean_db):
        mcp1 = FastMCP("instance-1")
        mcp2 = FastMCP("instance-2")
        from app.mcp.tools.mail import register as register_mail
        register_mail(mcp1, app)
        register_mail(mcp2, app)
        tools1 = mcp1._tool_manager._tools
        tools2 = mcp2._tool_manager._tools
        assert set(tools1.keys()) == set(tools2.keys())
        with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError("fail")):
            asyncio.run(tools1["mail_list_folders"].fn())
        assert len(tools1) == len(tools2)


class TestAuthErrorPropagation:
    def test_mcp_auth_error_returns_actual_code(self, mcp_all):
        from app.mcp.auth import McpAuthError
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=McpAuthError("NO_DEK", "No encryption key available")):
            result = asyncio.run(tools["mail_list_folders"].fn())
        data = json.loads(result)
        assert data["error"]["code"] == "NO_DEK"
        assert data["error"]["message"] == "No encryption key available"
        assert "McpAuthError" not in json.dumps(data)

    def test_scope_denied_returns_actual_code(self, mcp_all):
        from app.mcp.auth import McpAuthError
        tools = mcp_all["tools"]
        with patch(f"{MAIL}.resolve_write", side_effect=McpAuthError("SCOPE_DENIED", "This action requires the 'mail:write' permission.")):
            result = asyncio.run(tools["mail_send"].fn(
                to=["test@example.com"], subject="Test", body_plain="Hello",
            ))
        data = json.loads(result)
        assert data["error"]["code"] == "SCOPE_DENIED"
        assert "mail:write" in data["error"]["message"]
        assert "INTERNAL" not in data["error"]["code"]

    def test_auth_invalid_returns_actual_code(self, mcp_all):
        from app.mcp.auth import McpAuthError
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=McpAuthError("AUTH_INVALID", "Invalid or expired API token")):
            result = asyncio.run(tools["mail_list_folders"].fn())
        data = json.loads(result)
        assert data["error"]["code"] == "AUTH_INVALID"

    def test_unknown_auth_error_code_mapped_to_auth_error(self, mcp_all):
        from app.mcp.auth import McpAuthError
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=McpAuthError("SOME_NEW_CODE", "something")):
            result = asyncio.run(tools["mail_list_folders"].fn())
        data = json.loads(result)
        assert data["error"]["code"] == "AUTH_ERROR"

    def test_internal_error_does_not_leak_exception_details(self, mcp_all):
        tools = mcp_all["tools"]
        with patch(f"{MAIL}._get_cache_conn", side_effect=RuntimeError("database connection string: postgresql://admin:secret@db:5432")):
            result = asyncio.run(tools["mail_list_folders"].fn())
        data = json.loads(result)
        assert data["error"]["code"] == "INTERNAL_ERROR"
        assert "postgresql" not in data["error"]["message"]
        assert "secret" not in data["error"]["message"]
        assert "RuntimeError" not in data["error"]["message"]
        assert "details" not in data["error"]
