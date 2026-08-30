import contextlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from app.modules.chat.services import cache_db
from app.modules.chat.services.cache import get_cache_path
from tests.api.conftest import (
    auth_header,
    cleanup_cache_db,
    create_api_token,
    setup_cache_db,
)

OWN_ID = "@tester:locoroo.test"


@pytest.fixture()
def chat_api_env(app, api_customer):
    client, user_id, account_id = api_customer
    with app.app_context():
        from app.shared.db import db
        from app.shared.models.core import CustomerAccount, Domain

        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        domain = db.session.get(Domain, account.domain_id)
        assert domain is not None
        domain.matrix_host = "synapse"
        domain.matrix_port = 8008
        db.session.commit()
    cache_path = setup_cache_db(app, account_id, cache_path_fn=get_cache_path)
    conn = cache_db.open_cache(cache_path, "a" * 64)  # seeded with the API DEK
    cache_db.set_credentials(
        conn,
        matrix_user_id=OWN_ID,
        access_token="syt_tok",
        device_id="DEV1",
        password_encrypted="enc",
        homeserver_url="http://synapse:8008",
    )
    cache_db.upsert_room(conn, "!room1:server", name="General", is_public=True)
    cache_db.insert_message(
        conn,
        event_id="$m1",
        room_id="!room1:server",
        sender=OWN_ID,
        type="m.room.message",
        body="hello",
        content_json=json.dumps({"msgtype": "m.text", "body": "hello"}),
        origin_server_ts=1000,
    )
    conn.close()

    mock_matrix = MagicMock()
    mock_matrix.create_room.return_value = {"room_id": "!api-new:server"}
    mock_matrix.send_message.return_value = {"event_id": "$api-m1"}

    with app.app_context():
        token, _ = create_api_token(app, user_id, scopes=["chat:read", "chat:write"])
        limited_token, _ = create_api_token(app, user_id, name="no-chat", scopes=["mail:read"])

    def make_client_ignoring_token(base_url, access_token=None, user_id=None):
        return mock_matrix

    patcher = patch("app.api.controllers.chat.MatrixClient", side_effect=make_client_ignoring_token)
    patcher.start()
    yield {
        "client": client,
        "token": token,
        "limited_token": limited_token,
        "mock": mock_matrix,
        "cache_path": cache_path,
        "account_id": account_id,
    }
    patcher.stop()
    cleanup_cache_db(cache_path)


def test_list_rooms_happy_path(chat_api_env):
    env = chat_api_env
    resp = env["client"].get("/api/v1/chat/rooms", headers=auth_header(env["token"]))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"][0]["room_id"] == "!room1:server"
    assert body["data"][0]["display_name"] == "General"
    assert body["data"][0]["is_direct"] is False
    assert body["data"][0]["notification_count"] == 0


def test_list_rooms_requires_chat_scope(chat_api_env):
    env = chat_api_env
    resp = env["client"].get("/api/v1/chat/rooms", headers=auth_header(env["limited_token"]))
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "SCOPE_DENIED"


def test_list_rooms_unauthorized(chat_api_env):
    env = chat_api_env
    resp = env["client"].get("/api/v1/chat/rooms")
    assert resp.status_code == 401


def test_list_messages_and_404(chat_api_env):
    env = chat_api_env
    headers = auth_header(env["token"])
    resp = env["client"].get("/api/v1/chat/rooms/!room1:server/messages", headers=headers)
    assert resp.status_code == 200
    messages = resp.get_json()["data"]
    assert messages[0]["event_id"] == "$m1"
    assert messages[0]["content"]["body"] == "hello"

    resp = env["client"].get("/api/v1/chat/rooms/!missing:server/messages", headers=headers)
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "ROOM_NOT_FOUND"


def test_create_room_validation(chat_api_env):
    env = chat_api_env
    resp = env["client"].post("/api/v1/chat/rooms", headers=auth_header(env["token"]), json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "VALIDATION"


def test_create_room_happy_path(chat_api_env):
    env = chat_api_env
    resp = env["client"].post(
        "/api/v1/chat/rooms",
        headers=auth_header(env["token"]),
        json={"name": "API room"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["room_id"] == "!api-new:server"
    env["mock"].create_room.assert_called_once()


def test_send_message_happy_path(chat_api_env):
    env = chat_api_env
    resp = env["client"].post(
        "/api/v1/chat/rooms/!room1:server/messages",
        headers=auth_header(env["token"]),
        json={"body": "from the API"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["event_id"] == "$api-m1"


def test_send_message_empty_body(chat_api_env):
    env = chat_api_env
    resp = env["client"].post(
        "/api/v1/chat/rooms/!room1:server/messages",
        headers=auth_header(env["token"]),
        json={"body": ""},
    )
    assert resp.status_code == 422


def test_not_provisioned_returns_409(app, api_customer):
    client, user_id, account_id = api_customer
    cache_path = setup_cache_db(app, account_id, cache_path_fn=get_cache_path)
    conn = cache_db.open_cache(cache_path, "a" * 64)
    conn.close()  # schema only, no credentials row
    with app.app_context():
        token, _ = create_api_token(app, user_id, scopes=["chat:read", "chat:write"])
    try:
        resp = client.get("/api/v1/chat/rooms", headers=auth_header(token))
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "CHAT_NOT_PROVISIONED"
    finally:
        cleanup_cache_db(cache_path)
        with contextlib.suppress(OSError):
            os.unlink(cache_path)
