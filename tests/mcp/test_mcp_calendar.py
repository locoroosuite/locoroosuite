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


CALENDAR = "app.mcp.tools.calendar"
CALENDAR_CACHE_DB = "app.modules.calendar.services.cache_db"
CALENDAR_CALDAV = "app.modules.calendar.services.caldav"
UI_EVENTS = "app.shared.ui_events"


@pytest.fixture()
def mcp_calendar(app, _clean_db):
    user_id = None
    account_id = None

    with app.app_context():
        user = User(email="mcp-cal@example.com", role="customer", is_active=True)
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
            email_address="mcp-cal@example.com",
            auth_type="password",
            username="mcp-cal@example.com",
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
            user_id, dek, "test-token", ["calendar:read", "calendar:write"]
        )

    from app.mcp.auth import set_current_token
    set_current_token(token_value)

    mcp = FastMCP("test-calendar")
    from app.mcp.tools.calendar import register as register_calendar
    register_calendar(mcp, app)

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


def _mock_calendar_row(cal_id=1, uid="cal-uid-1", displayname="Work",
                       color="#4285f4", is_default=True, href="/cal1/",
                       order_index=0, description="", is_visible=1,
                       last_sync_at=None):
    keys = ["id", "uid", "href", "displayname", "color", "description",
            "is_visible", "is_default", "order_index", "last_sync_at"]
    vals = {
        "id": cal_id, "uid": uid, "href": href, "displayname": displayname,
        "color": color, "description": description,
        "is_visible": is_visible, "is_default": is_default,
        "order_index": order_index, "last_sync_at": last_sync_at,
    }
    r = MagicMock()
    r.__getitem__ = lambda self, k: vals[k]
    r.keys = lambda: keys
    r.get = lambda k, default=None: vals.get(k, default)
    return r


def _mock_event_row(event_id=1, uid="evt-uid-1", summary="Team Meeting",
                    description="", location="Room 1",
                    dtstart="2025-06-01T10:00:00", dtend="2025-06-01T11:00:00",
                    all_day=0, status="CONFIRMED", calendar_id=1,
                    raw_ical="", href="/evt1.ics", etag="e1",
                    updated_at="2025-01-01T00:00:00",
                    calendar_color="#4285f4", calendar_name="Work"):
    keys = ["id", "uid", "href", "etag", "calendar_id", "summary",
            "description", "location", "dtstart", "dtend", "all_day",
            "status", "raw_ical", "updated_at",
            "calendar_color", "calendar_name"]
    vals = {
        "id": event_id, "uid": uid, "href": href, "etag": etag,
        "calendar_id": calendar_id, "summary": summary,
        "description": description, "location": location,
        "dtstart": dtstart, "dtend": dtend, "all_day": all_day,
        "status": status, "raw_ical": raw_ical,
        "updated_at": updated_at,
        "calendar_color": calendar_color, "calendar_name": calendar_name,
    }
    r = MagicMock()
    r.__getitem__ = lambda self, k: vals[k]
    r.keys = lambda: keys
    r.get = lambda k, default=None: vals.get(k, default)
    return r


class TestCalendarListTools:
    def test_list_calendars(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        mock_row = _mock_calendar_row(1, displayname="Work")
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_all_calendars", return_value=[mock_row]):
                result = asyncio.run(tools["calendar_list_calendars"].fn())
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Work"
        assert data[0]["color"] == "#4285f4"
        assert data[0]["is_default"] is True

    def test_list_calendars_empty(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_all_calendars", return_value=[]):
                result = asyncio.run(tools["calendar_list_calendars"].fn())
        data = json.loads(result)["data"]
        assert data == []

    def test_list_events(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        mock_cal = _mock_calendar_row(1)
        mock_evt = _mock_event_row(1, summary="Standup", calendar_id=1)
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_calendar", return_value=mock_cal):
                with patch(f"{CALENDAR_CACHE_DB}.get_events_range", return_value=[mock_evt]):
                    result = asyncio.run(tools["calendar_list_events"].fn(calendar_id=1))
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["summary"] == "Standup"

    def test_list_events_calendar_not_found(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_calendar", return_value=None):
                result = asyncio.run(tools["calendar_list_events"].fn(calendar_id=999))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_get_event(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        mock_evt = _mock_event_row(1, summary="Review", location="Zoom")
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_event", return_value=mock_evt):
                result = asyncio.run(tools["calendar_get_event"].fn(event_id=1))
        data = json.loads(result)["data"]
        assert data["id"] == 1
        assert data["summary"] == "Review"
        assert data["location"] == "Zoom"

    def test_get_event_not_found(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_event", return_value=None):
                result = asyncio.run(tools["calendar_get_event"].fn(event_id=999))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_search_events(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        mock_evt = _mock_event_row(1, summary="Sprint Planning")
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.search_events", return_value=[mock_evt]):
                result = asyncio.run(tools["calendar_search_events"].fn(q="sprint"))
        data = json.loads(result)["data"]
        assert len(data) == 1

    def test_check_free_busy(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        mock_evt = _mock_event_row(1, summary="Busy Block",
                                    dtstart="2025-06-01T10:00:00",
                                    dtend="2025-06-01T11:00:00")
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_conflicting_events", return_value=[mock_evt]):
                result = asyncio.run(
                    tools["calendar_check_free_busy"].fn(
                        calendar_ids=[1],
                        start="2025-06-01T09:00:00",
                        end="2025-06-01T12:00:00",
                    )
                )
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["summary"] == "Busy Block"

    def test_check_free_busy_no_conflicts(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_conflicting_events", return_value=[]):
                result = asyncio.run(
                    tools["calendar_check_free_busy"].fn(
                        calendar_ids=[1],
                        start="2025-06-01T09:00:00",
                        end="2025-06-01T12:00:00",
                    )
                )
        data = json.loads(result)["data"]
        assert data == []


class TestCalendarMutationTools:
    def test_delete_calendar_requires_confirmation(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        result = asyncio.run(
            tools["calendar_delete_calendar"].fn(calendar_id=1, confirm=False)
        )
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_delete_calendar(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        mock_cal = _mock_calendar_row(1)
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_calendar", return_value=mock_cal):
                with patch(f"{CALENDAR_CACHE_DB}.delete_calendar_by_id"):
                    with patch(f"{UI_EVENTS}.push_ui_event"):
                        result = asyncio.run(
                            tools["calendar_delete_calendar"].fn(calendar_id=1, confirm=True)
                        )
        data = json.loads(result)
        assert "error" not in data

    def test_delete_calendar_not_found(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_calendar", return_value=None):
                result = asyncio.run(
                    tools["calendar_delete_calendar"].fn(calendar_id=999, confirm=True)
                )
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_update_calendar(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        mock_cal = _mock_calendar_row(1)
        updated_cal = _mock_calendar_row(1, displayname="Personal", color="#ff0000")
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_calendar", side_effect=[mock_cal, updated_cal]):
                with patch(f"{CALENDAR_CACHE_DB}.update_calendar"):
                    with patch(f"{CALENDAR}._get_caldav_session"):
                        with patch(f"{CALENDAR_CALDAV}.update_calendar_props"):
                            with patch(f"{UI_EVENTS}.push_ui_event"):
                                result = asyncio.run(
                                    tools["calendar_update_calendar"].fn(
                                        calendar_id=1, name="Personal", color="#ff0000"
                                    )
                                )
        data = json.loads(result)["data"]
        assert data["name"] == "Personal"
        assert data["color"] == "#ff0000"

    def test_update_calendar_not_found(self, mcp_calendar):
        tools = mcp_calendar["tools"]
        with patch(f"{CALENDAR}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{CALENDAR_CACHE_DB}.get_calendar", return_value=None):
                result = asyncio.run(
                    tools["calendar_update_calendar"].fn(calendar_id=999, name="X")
                )
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"


class TestCalendarResponseShape:
    def test_calendar_to_dict_has_expected_fields(self):
        from app.mcp.tools.calendar import _calendar_to_dict
        keys = ["id", "uid", "href", "displayname", "color", "description",
                "is_visible", "is_default", "order_index", "last_sync_at"]
        vals = {
            "id": 1, "uid": "uid-1", "href": "/c1/", "displayname": "Work",
            "color": "#4285f4", "description": "", "is_visible": 1,
            "is_default": 1, "order_index": 0, "last_sync_at": None,
        }
        r = MagicMock()
        r.__getitem__ = lambda self, k: vals[k]
        r.keys = lambda: keys
        r.get = lambda k, default=None: vals.get(k, default)
        result = _calendar_to_dict(r)
        assert result["id"] == 1
        assert result["uid"] == "uid-1"
        assert result["name"] == "Work"
        assert result["color"] == "#4285f4"
        assert result["is_default"] is True

    def test_event_to_dict_has_expected_fields(self):
        from app.mcp.tools.calendar import _event_to_dict
        keys = ["id", "uid", "href", "etag", "calendar_id", "summary",
                "description", "location", "dtstart", "dtend", "all_day",
                "status", "raw_ical", "updated_at"]
        vals = {
            "id": 1, "uid": "e-uid", "href": "/e1.ics", "etag": "e1",
            "calendar_id": 1, "summary": "Meet", "description": "desc",
            "location": "Room", "dtstart": "2025-06-01T10:00:00",
            "dtend": "2025-06-01T11:00:00", "all_day": 0,
            "status": "CONFIRMED", "raw_ical": "",
            "updated_at": "2025-01-01T00:00:00",
        }
        r = MagicMock()
        r.__getitem__ = lambda self, k: vals[k]
        r.keys = lambda: keys
        r.get = lambda k, default=None: vals.get(k, default)
        result = _event_to_dict(r)
        assert result["id"] == 1
        assert result["summary"] == "Meet"
        assert result["start"] == "2025-06-01T10:00:00"
        assert result["end"] == "2025-06-01T11:00:00"
        assert result["is_all_day"] is False
