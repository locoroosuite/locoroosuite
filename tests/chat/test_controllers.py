import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.chat.services import cache_db
from app.modules.chat.services.cache import get_cache_path
from app.modules.chat.services.matrix import MatrixError
from app.shared.models.core import CustomerAccount, Domain

OWN_ID = "@tester:locoroo.test"


@pytest.fixture()
def seeded_client(app, authed_client):
    """authed_client + real chat cache with creds/rooms + mocked Matrix client."""
    client, user_id, account_id = authed_client
    from app.shared.db import db
    from app.shared.keys import get_user_key

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        domain = db.session.get(Domain, account.domain_id)
        assert domain is not None
        domain.matrix_host = "synapse"
        domain.matrix_port = 8008
        domain.matrix_shared_secret = "dev-matrix-shared-secret"
        db.session.commit()
        path = get_cache_path(account)
        conn = cache_db.open_cache(path, get_user_key(user_id))
        cache_db.set_credentials(
            conn,
            matrix_user_id=OWN_ID,
            access_token="syt_tok",
            device_id="DEV1",
            password_encrypted="enc",
            homeserver_url="http://synapse:8008",
        )
        cache_db.upsert_room(conn, "!room1:server", name="General", is_public=True)
        cache_db.upsert_member(conn, "!room1:server", OWN_ID, displayname="Tester")
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
        conn.commit()
        conn.close()

    mock_matrix = MagicMock()
    mock_matrix.user_id = OWN_ID
    mock_matrix.create_room.return_value = {"room_id": "!new:server"}
    mock_matrix.send_message.return_value = {"event_id": "$new1"}
    mock_matrix.send_event.return_value = {"event_id": "$up1"}
    mock_matrix.send_reaction.return_value = {"event_id": "$react1"}
    mock_matrix.send_edit.return_value = {"event_id": "$edit1"}
    mock_matrix.redact.return_value = {"event_id": "$redact1"}
    mock_matrix.sync.return_value = {"next_batch": "s2", "rooms": {}}
    mock_matrix.whoami.return_value = {"user_id": OWN_ID}

    def fake_ensure(account, domain, user_id_session):
        conn = cache_db.open_cache(path, get_user_key(user_id))
        creds = cache_db.get_credentials(conn) or {}
        return conn, mock_matrix, creds

    import os

    with patch(
        "app.modules.chat.services.provisioning.ensure_chat_client",
        side_effect=fake_ensure,
    ):
        yield client, mock_matrix, path, user_id
    if os.path.exists(path):
        os.unlink(path)


def test_index_requires_login(client):
    resp = client.get("/app/chat/", follow_redirects=False)
    assert resp.status_code == 302


def test_index_renders(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.get("/app/chat/")
    assert resp.status_code == 200
    assert b"chat-root" in resp.data


def test_state_returns_rooms(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.get("/app/chat/api/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["identity"]["matrix_user_id"] == OWN_ID
    assert any(r["room_id"] == "!room1:server" for r in data["rooms"])


def test_room_messages(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.get("/app/chat/api/rooms/!room1:server/messages")
    assert resp.status_code == 200
    data = resp.get_json()
    assert [m["event_id"] for m in data["messages"]] == ["$m1"]


def test_room_messages_not_found(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.get("/app/chat/api/rooms/!unknown:server/messages")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "ROOM_NOT_FOUND"


def test_create_room_validation(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/rooms", json={"topic": "no name"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "VALIDATION"

    resp = client.post("/app/chat/api/rooms", json={"is_direct": True})
    assert resp.status_code == 400


def test_create_room_happy(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post(
        "/app/chat/api/rooms", json={"name": "New room", "topic": "T", "is_public": True}
    )
    assert resp.status_code == 201
    assert resp.get_json()["room"]["room_id"] == "!new:server"
    mock.create_room.assert_called_once()


def test_send_message(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/rooms/!room1:server/send", json={"body": "hi"})
    assert resp.status_code == 201
    assert resp.get_json()["event_id"] == "$new1"
    mock.send_message.assert_called_once()

    resp = client.post("/app/chat/api/rooms/!room1:server/send", json={"body": "  "})
    assert resp.status_code == 400


def test_react_toggle(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/messages/$m1/react", json={"key": "👍"})
    assert resp.status_code == 201
    assert resp.get_json()["removed"] is False
    mock.send_reaction.assert_called_once()

    resp = client.post("/app/chat/api/messages/$m1/react", json={"key": "👍"})
    assert resp.status_code == 200
    assert resp.get_json()["removed"] is True
    mock.redact.assert_called_once()


def test_edit_and_redact(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/messages/$m1/edit", json={"body": "new text"})
    assert resp.status_code == 200
    mock.send_edit.assert_called_once()

    resp = client.post("/app/chat/api/messages/$m1/redact")
    assert resp.status_code == 200
    mock.redact.assert_called_once()


def test_matrix_error_maps_to_502(seeded_client):
    client, mock, _path, _user_id = seeded_client
    mock.send_message.side_effect = MatrixError("M_UNKNOWN", "boom", 500)
    resp = client.post("/app/chat/api/rooms/!room1:server/send", json={"body": "hi"})
    assert resp.status_code == 502
    assert resp.get_json()["error"]["code"] == "M_UNKNOWN"


def test_sync_now(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/sync-now")
    assert resp.status_code == 200
    assert "rooms" in resp.get_json()
    mock.sync.assert_called_once()


def test_invite_to_room(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/rooms/!room1:server/invite", json={"user_id": "@peer:server"})
    assert resp.status_code == 200
    mock.invite.assert_called_once_with("!room1:server", "@peer:server")

    members = client.get("/app/chat/api/rooms/!room1:server/members").get_json()["members"]
    assert any(m["user_id"] == "@peer:server" for m in members)


def test_invite_requires_user_id(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/rooms/!room1:server/invite", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "VALIDATION"
    mock.invite.assert_not_called()


def test_user_search(seeded_client):
    client, mock, _path, _user_id = seeded_client
    mock.user_directory_search.return_value = {
        "results": [{"user_id": "@peer:server", "display_name": "Peer"}]
    }
    resp = client.get("/app/chat/api/users?q=peer")
    assert resp.status_code == 200
    assert resp.get_json()["users"][0]["user_id"] == "@peer:server"

    resp = client.get("/app/chat/api/users?q=p")
    assert resp.status_code == 200
    assert resp.get_json()["users"] == []


def test_state_unconfigured_domain_returns_503(app, authed_client):
    client, _user_id, _account_id = authed_client
    resp = client.get("/app/chat/api/state")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "MATRIX_NOT_CONFIGURED"
    assert "Admin" in resp.get_json()["error"]["message"]


def _add_peer_account(app, email):
    from sqlalchemy import insert

    from app.shared.db import db
    from app.shared.models.core import CustomerAccount, Domain, User

    with app.app_context():
        main_domain = Domain.query.first()
        assert main_domain is not None
        db.session.execute(insert(User).values(role="customer", email=email))
        peer = User.query.filter_by(email=email).first()
        assert peer is not None
        db.session.execute(
            insert(CustomerAccount).values(
                customer_id=peer.id,
                domain_id=main_domain.id,
                email_address=email,
                username=email.split("@", 1)[0],
            )
        )
        db.session.commit()


def test_dm_by_email(app, seeded_client):
    client, mock, _path, _user_id = seeded_client
    _add_peer_account(app, "test4@test.localhost")

    mock.create_room.return_value = {"room_id": "!dm9:server"}
    mock.get_displayname.return_value = "test4"
    with patch(
        "app.modules.chat.controllers.api.ensure_matrix_user",
        return_value={"matrix_user_id": "@test4:locoroo.test", "created": False},
    ):
        resp = client.post("/app/chat/api/dm", json={"email": "test4@test.localhost"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["room"]["room_id"] == "!dm9:server"
    assert data["peer"]["email"] == "test4@test.localhost"
    assert data["peer"]["matrix_user_id"] == "@test4:locoroo.test"
    mock.create_room.assert_called_once()
    assert mock.create_room.call_args.kwargs.get("is_direct") is True


def test_dm_by_email_unknown_account(seeded_client):
    client, mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/dm", json={"email": "nobody@test.localhost"})
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "NO_SUCH_ACCOUNT"
    mock.create_room.assert_not_called()


def test_dm_by_email_validation(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.post("/app/chat/api/dm", json={"email": "not-an-address"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "VALIDATION"


def test_invite_by_email(app, seeded_client):
    client, mock, _path, _user_id = seeded_client
    _add_peer_account(app, "test5@test.localhost")

    with patch(
        "app.modules.chat.controllers.api.ensure_matrix_user",
        return_value={"matrix_user_id": "@test5:locoroo.test", "created": False},
    ):
        resp = client.post(
            "/app/chat/api/rooms/!room1:server/invite", json={"email": "test5@test.localhost"}
        )
    assert resp.status_code == 200
    assert resp.get_json()["user_id"] == "@test5:locoroo.test"
    mock.invite.assert_called_once_with("!room1:server", "@test5:locoroo.test")


def test_stream_requires_login(client):
    resp = client.get("/app/chat/api/stream")
    assert resp.status_code == 302


def test_stream_returns_503_when_matrix_unconfigured(app, seeded_client):
    client, _mock, _path, _user_id = seeded_client
    with patch(
        "app.modules.chat.services.provisioning.ensure_chat_client",
        side_effect=MatrixError("MATRIX_NOT_CONFIGURED", "Matrix is not configured"),
    ):
        resp = client.get("/app/chat/api/stream")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "MATRIX_NOT_CONFIGURED"


def test_stream_survives_unexpected_sync_errors(app, seeded_client):
    client, mock_matrix, _path, _user_id = seeded_client
    mock_matrix.sync.side_effect = [
        {"next_batch": "s2", "rooms": {}},
        RuntimeError("cache exploded"),
        RuntimeError("cache exploded"),
        RuntimeError("cache exploded"),
        RuntimeError("cache exploded"),
        RuntimeError("cache exploded"),
    ]
    with patch("app.modules.chat.controllers.stream._ERROR_BACKOFF_S", 0):
        resp = client.get("/app/chat/api/stream", buffered=True)
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/event-stream")
    body = resp.get_data(as_text=True)
    assert "event: chat_ready" in body
    assert "event: chat_sync" in body
    assert "SYNC_FAILED" in body
    assert mock_matrix.sync.call_count == 6
