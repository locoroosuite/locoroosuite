"""U27: AI activity visibility — REST hook emission, the cross-process
publish endpoint, and the SSE payload contract."""

import pytest

from tests.api.conftest import auth_header, cleanup_cache_db, create_api_token, setup_cache_db


@pytest.fixture()
def api_env(app, api_customer):
    client, user_id, account_id = api_customer
    with app.app_context():
        token_value, _ = create_api_token(app, user_id)
    cache_path = setup_cache_db(app, account_id)
    yield client, token_value, user_id, account_id, cache_path
    cleanup_cache_db(cache_path)


class _EventRecorder:
    def __init__(self):
        self.events = []

    def __call__(self, user_id, event_type, data):
        self.events.append({"user_id": user_id, "type": event_type, "data": data})

    def of_type(self, event_type):
        return [e for e in self.events if e["type"] == event_type]


@pytest.fixture()
def recorder():
    from app.api import openapi as _openapi
    from app.shared import events

    _openapi._coalesce_state.clear()
    rec = _EventRecorder()
    events._subscribers.append(rec)
    yield rec
    events._subscribers.remove(rec)


def _set_secret(app, account_id, dek="a" * 64):
    from app.modules.mail.services.secrets import encrypt_with_key
    from app.shared.db import db as _db
    from app.shared.models.core import CustomerAccount

    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        assert account is not None
        account.encrypted_secret = encrypt_with_key("testpass", dek)
        _db.session.commit()


class TestApiHookEmitsAiActivity:
    def test_authenticated_get_emits(self, api_env, recorder):
        client, token, user_id, _account_id, _cache = api_env
        resp = client.get("/api/v1/mail/folders", headers=auth_header(token))
        assert resp.status_code == 200
        ai_events = recorder.of_type("ai_activity")
        assert ai_events, "expected an ai_activity event for the API call"
        event = ai_events[-1]
        assert event["user_id"] == user_id
        assert event["data"]["tool"] == "mail_list_folders"
        assert event["data"]["status"] == "ok"
        assert isinstance(event["data"]["duration_ms"], int)

    def test_byte_stream_endpoints_do_not_emit(self, api_env, recorder, app):
        client, token, _user_id, account_id, _cache = api_env
        _set_secret(app, account_id)
        resp = client.get("/api/v1/mail/messages/999999/raw", headers=auth_header(token))
        assert resp.status_code == 404
        resp = client.get("/api/v1/mail/messages/999999/attachments/0", headers=auth_header(token))
        assert resp.status_code == 404
        assert recorder.of_type("ai_activity") == []

    def test_error_status_reported(self, api_env, recorder):
        client, token, _user_id, _account_id, _cache = api_env
        resp = client.post(
            "/api/v1/mail/messages",
            json={"to": [], "subject": "x", "body_plain": "y"},
            headers=auth_header(token),
        )
        assert resp.status_code == 400
        ai_events = recorder.of_type("ai_activity")
        assert ai_events
        assert ai_events[-1]["data"]["status"] == "http_400"

    def test_identical_calls_coalesce_last_wins(self, api_env, recorder, monkeypatch):
        """U27.3: identical consecutive events within the window merge — the
        first emits immediately, suppressed repeats flush as one merged
        event with the accumulated count (never invisible)."""
        import time

        from app.api import openapi

        monkeypatch.setattr(openapi, "_COALESCE_WINDOW_S", 0.2)
        client, token, _user_id, _account_id, _cache = api_env
        for _ in range(3):
            client.get("/api/v1/mail/folders", headers=auth_header(token))
        first_burst = recorder.of_type("ai_activity")
        assert len(first_burst) == 1  # immediate emission
        # wait for the flush timer (window + margin)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if len(recorder.of_type("ai_activity")) >= 2:
                break
            time.sleep(0.05)
        merged = recorder.of_type("ai_activity")
        assert len(merged) == 2
        assert merged[1]["data"]["count"] == 3  # last-wins with the burst count

    def test_unauthenticated_requests_do_not_emit(self, api_env, recorder):
        client = api_env[0]
        client.get("/api/v1/mail/folders")
        assert recorder.of_type("ai_activity") == []


class TestEventsPublishEndpoint:
    def test_publish_ai_activity_requires_auth(self, api_env):
        client = api_env[0]
        resp = client.post(
            "/api/v1/events/publish",
            json={"type": "ai_activity", "tool": "x"},
        )
        assert resp.status_code == 401

    def test_publish_ai_activity_reaches_own_stream(self, api_env, recorder):
        client, token, user_id, _account_id, _cache = api_env
        resp = client.post(
            "/api/v1/events/publish",
            json={
                "type": "ai_activity",
                "tool": "mail_save_draft",
                "summary": "mail_save_draft (to=[a@example.com])",
                "status": "ok",
                "duration_ms": 12,
                "account_id": 1,
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        ai_events = recorder.of_type("ai_activity")
        assert ai_events
        assert ai_events[-1]["user_id"] == user_id
        assert ai_events[-1]["data"]["tool"] == "mail_save_draft"
        assert ai_events[-1]["data"]["account_id"] == 1

    def test_publish_ui_change(self, api_env, recorder):
        client, token, user_id, _account_id, _cache = api_env
        resp = client.post(
            "/api/v1/events/publish",
            json={
                "type": "ui_change",
                "module": "mail",
                "action": "draft_saved",
                "data": {"account_id": 1, "snippet": "hello"},
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        ui_events = [
            e for e in recorder.of_type("ui_change") if e["data"].get("action") == "draft_saved"
        ]
        assert ui_events
        assert ui_events[-1]["user_id"] == user_id

    def test_publish_rejects_unknown_type(self, api_env):
        client, token, _user_id, _account_id, _cache = api_env
        resp = client.post(
            "/api/v1/events/publish",
            json={"type": "new_mail", "data": {}},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_publish_rejects_missing_tool(self, api_env):
        client, token, _user_id, _account_id, _cache = api_env
        resp = client.post(
            "/api/v1/events/publish",
            json={"type": "ai_activity"},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_publish_never_leaks_customer_id_into_payload(self, api_env, recorder):
        """U27.9: the bridge stamps customer_id for routing, but the endpoint
        strips it from the re-published payload."""
        client, token, _user_id, _account_id, _cache = api_env
        resp = client.post(
            "/api/v1/events/publish",
            json={
                "type": "ui_change",
                "module": "mail",
                "action": "sync",
                "data": {"customer_id": 12345, "x": 1},
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        ui_events = [e for e in recorder.of_type("ui_change") if e["data"].get("action") == "sync"]
        assert ui_events
        assert "customer_id" not in ui_events[-1]["data"]


class TestMcpEventBridge:
    def test_local_mode_pushes_in_process(self, recorder, monkeypatch):
        from app.mcp import events_bridge

        monkeypatch.setattr(events_bridge, "_BRIDGE_MODE", "local")
        events_bridge.bridge_ai_activity(7, "mail_send", "mail_send (to=[x@y.z])", account_id=3)
        ai_events = recorder.of_type("ai_activity")
        assert ai_events
        assert ai_events[-1]["data"]["tool"] == "mail_send"

    def test_relay_mode_defers_to_http(self, recorder, monkeypatch):
        """Default mode relays cross-process only — no in-process push (the
        MCP process has no SSE consumers; double-push is a U27.4 defect)."""
        from app.mcp import events_bridge

        relayed = []

        monkeypatch.setattr(events_bridge, "_BRIDGE_MODE", "relay")
        monkeypatch.setattr(events_bridge, "_relay_async", lambda payload: relayed.append(payload))
        events_bridge.bridge_ai_activity(7, "mail_send", "summary", account_id=3)
        events_bridge.bridge_ui_event(7, "mail", "draft_saved", {"account_id": 3})
        assert recorder.of_type("ai_activity") == []
        assert relayed and relayed[0]["type"] == "ai_activity"
        assert relayed[1]["type"] == "ui_change"
        assert relayed[1]["data"]["customer_id"] == 7

    def test_off_mode_is_noop(self, recorder, monkeypatch):
        from app.mcp import events_bridge

        monkeypatch.setattr(events_bridge, "_BRIDGE_MODE", "off")
        events_bridge.bridge_ai_activity(7, "mail_send", "summary")
        events_bridge.bridge_ui_event(7, "mail", "x", {})
        assert recorder.of_type("ai_activity") == []

    def test_env_flags_parse(self, monkeypatch):
        import importlib

        monkeypatch.setenv("LR_MCP_EVENT_BRIDGE", "0")
        mod = importlib.reload(importlib.import_module("app.mcp.events_bridge"))
        assert mod._BRIDGE_MODE == "off"
        monkeypatch.setenv("LR_MCP_EVENT_BRIDGE", "local")
        mod = importlib.reload(mod)
        assert mod._BRIDGE_MODE == "local"
        monkeypatch.delenv("LR_MCP_EVENT_BRIDGE")
        mod = importlib.reload(mod)
        assert mod._BRIDGE_MODE == "relay"
