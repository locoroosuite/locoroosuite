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


CONTACTS = "app.mcp.tools.contacts"
CONTACTS_CACHE_DB = "app.modules.contacts.services.cache_db"
CONTACTS_CARDDAV = "app.modules.contacts.services.carddav"
CONTACTS_VCARD = "app.modules.contacts.services.vcard"
UI_EVENTS = "app.shared.ui_events"


@pytest.fixture()
def mcp_contacts(app, _clean_db):
    user_id = None
    account_id = None

    with app.app_context():
        user = User(email="mcp-contacts@example.com", role="customer", is_active=True)
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
        )
        _db.session.add(domain)
        _db.session.flush()

        dek = generate_dek()
        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address="mcp-contacts@example.com",
            auth_type="password",
            username="mcp-contacts@example.com",
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
            user_id, dek, "test-token", ["contacts:read", "contacts:write"]
        )

    from app.mcp.auth import set_current_token
    set_current_token(token_value)

    mcp = FastMCP("test-contacts")
    from app.mcp.tools.contacts import register as register_contacts
    register_contacts(mcp, app)

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


def _mock_contact_row(contact_id=1, uid="uid-1", fn="Alice Smith",
                      email_work="alice@example.com", email_home="",
                      tel_work="+1234", tel_cell="", tel_home="",
                      org="Acme", title="Engineer", note="",
                      raw_vcard="", href="/alice.vcf", etag="etag1",
                      updated_at="2025-01-01T00:00:00"):
    keys = ["id", "uid", "href", "etag", "fn", "last_name", "first_name",
            "email_work", "email_home", "tel_work", "tel_home", "tel_cell",
            "org", "title", "note", "raw_vcard", "updated_at"]
    vals = {
        "id": contact_id, "uid": uid, "href": href, "etag": etag,
        "fn": fn, "last_name": "", "first_name": "",
        "email_work": email_work, "email_home": email_home,
        "tel_work": tel_work, "tel_home": tel_home, "tel_cell": tel_cell,
        "org": org, "title": title, "note": note,
        "raw_vcard": raw_vcard, "updated_at": updated_at,
    }
    r = MagicMock()
    r.__getitem__ = lambda self, k: vals[k]
    r.keys = lambda: keys
    r.get = lambda k, default=None: vals.get(k, default)
    return r


class TestContactsListTools:
    def test_list_contacts(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        mock_row = _mock_contact_row(1, fn="Alice Smith", email_work="alice@example.com")
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.list_contacts", return_value=[mock_row]):
                with patch(f"{CONTACTS_CACHE_DB}.count_contacts", return_value=1):
                    result = asyncio.run(tools["contacts_list"].fn())
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["fn"] == "Alice Smith"
        assert data[0]["email_work"] == "alice@example.com"

    def test_list_contacts_empty(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.list_contacts", return_value=[]):
                with patch(f"{CONTACTS_CACHE_DB}.count_contacts", return_value=0):
                    result = asyncio.run(tools["contacts_list"].fn())
        data = json.loads(result)["data"]
        assert data == []

    def test_list_contacts_with_search(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        mock_row = _mock_contact_row(1, fn="Bob Jones")
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.search_contacts", return_value=[mock_row]):
                with patch(f"{CONTACTS_CACHE_DB}.count_contacts", return_value=1):
                    result = asyncio.run(tools["contacts_list"].fn(q="bob"))
        data = json.loads(result)["data"]
        assert len(data) == 1

    def test_get_contact(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        mock_row = _mock_contact_row(1, fn="Alice Smith", raw_vcard="BEGIN:VCARD\nFN:Alice Smith\nEND:VCARD")
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.get_contact", return_value=mock_row):
                result = asyncio.run(tools["contacts_get"].fn(contact_id=1))
        data = json.loads(result)["data"]
        assert data["id"] == 1
        assert data["fn"] == "Alice Smith"
        assert "vcard_raw" in data

    def test_get_contact_not_found(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.get_contact", return_value=None):
                result = asyncio.run(tools["contacts_get"].fn(contact_id=999))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_search_contacts(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        raw_row = {"fn": "Alice", "emails": [{"email": "alice@example.com"}]}
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: raw_row[k]
        mock_row.keys = lambda: list(raw_row.keys())
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.search_contacts_api", return_value=[mock_row]):
                result = asyncio.run(tools["contacts_search"].fn(q="alice"))
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Alice"


class TestContactsMutationTools:
    def test_delete_contact(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        mock_row = _mock_contact_row(1, uid="uid-del")
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.get_contact", return_value=mock_row):
                with patch(f"{CONTACTS_CACHE_DB}.delete_contact_by_uid"):
                    with patch(f"{UI_EVENTS}.push_ui_event"):
                        result = asyncio.run(tools["contacts_delete"].fn(contact_id=1))
        data = json.loads(result)
        assert "error" not in data

    def test_delete_contact_not_found(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.get_contact", return_value=None):
                result = asyncio.run(tools["contacts_delete"].fn(contact_id=999))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_bulk_delete_validates_items(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        result = asyncio.run(tools["contacts_bulk_delete"].fn(items=[]))
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_bulk_delete_success(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        mock_row = _mock_contact_row(1, uid="uid-bd")
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.get_contact", return_value=mock_row):
                with patch(f"{CONTACTS_CACHE_DB}.delete_contact_by_uid"):
                    with patch(f"{UI_EVENTS}.push_ui_event"):
                        result = asyncio.run(
                            tools["contacts_bulk_delete"].fn(items=[{"contact_id": 1}])
                        )
        data = json.loads(result)["data"]
        assert len(data["succeeded"]) == 1
        assert data["succeeded"][0]["contact_id"] == 1

    def test_bulk_delete_partial_failure(self, mcp_contacts):
        tools = mcp_contacts["tools"]
        mock_row = _mock_contact_row(1, uid="uid-ok")
        with patch(f"{CONTACTS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CONTACTS_CACHE_DB}.get_contact", side_effect=[mock_row, None]):
                with patch(f"{CONTACTS_CACHE_DB}.delete_contact_by_uid"):
                    with patch(f"{UI_EVENTS}.push_ui_event"):
                        result = asyncio.run(
                            tools["contacts_bulk_delete"].fn(
                                items=[{"contact_id": 1}, {"contact_id": 999}]
                            )
                        )
        data = json.loads(result)["data"]
        assert len(data["succeeded"]) == 1
        assert len(data["failed"]) == 1


class TestContactsResponseShape:
    def test_contact_to_dict_has_expected_fields(self):
        from app.mcp.tools.contacts import _contact_to_dict
        keys = ["id", "uid", "href", "etag", "fn", "last_name", "first_name",
                "email_work", "email_home", "tel_work", "tel_home", "tel_cell",
                "org", "title", "note", "raw_vcard", "updated_at"]
        vals = {
            "id": 1, "uid": "uid-1", "href": "/a.vcf", "etag": "e1",
            "fn": "Test", "last_name": "", "first_name": "",
            "email_work": "t@e.com", "email_home": "",
            "tel_work": "", "tel_home": "", "tel_cell": "",
            "org": "", "title": "", "note": "",
            "raw_vcard": "", "updated_at": "2025-01-01T00:00:00",
        }
        r = MagicMock()
        r.__getitem__ = lambda self, k: vals[k]
        r.keys = lambda: keys
        r.get = lambda k, default=None: vals.get(k, default)
        result = _contact_to_dict(r)
        assert "id" in result
        assert "uid" in result
        assert "fn" in result
        assert "email_work" in result
        assert "phone_work" in result
        assert "organization" in result
