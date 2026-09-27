import json
from unittest.mock import patch

from app.modules.chat.services import cache_db
from app.modules.chat.services.matrix import MatrixError
from app.shared.models.core import Domain

# seeded_client (shared by the chat controller tests) lives in conftest.py.

OWN_ID = "@tester:locoroo.test"


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
    # U25.18: status + receipts are part of the response contract.
    assert data["messages"][0]["status"] is None  # group room: no ticks
    assert data["receipts"] == []


def test_room_messages_dm_statuses_and_receipts(app, seeded_client):
    client, _mock, path, user_id = seeded_client
    from app.shared.keys import get_user_key

    with app.app_context():
        conn = cache_db.open_cache(path, get_user_key(user_id))
        cache_db.upsert_room(conn, "!dm:server", is_direct=True)
        cache_db.upsert_member(conn, "!dm:server", OWN_ID)
        cache_db.upsert_member(conn, "!dm:server", "@peer:server", displayname="Peer")
        for event_id, ts in (("$own1", 1000), ("$own2", 2000)):
            cache_db.insert_message(
                conn,
                event_id=event_id,
                room_id="!dm:server",
                sender=OWN_ID,
                type="m.room.message",
                body=event_id,
                content_json=json.dumps({"msgtype": "m.text", "body": event_id}),
                origin_server_ts=ts,
            )
        # The peer read up to $own1.
        cache_db.upsert_receipt(conn, "!dm:server", "@peer:server", "m.read", "$own1", 1500)
        from app.modules.chat.services import receipts

        # The peer's device ingested $own2 (delivered, not read).
        receipts.record_deliveries(
            conn,
            "@peer:server",
            {"messages": {"!dm:server": [{"event_id": "$own2", "sender": OWN_ID}]}},
        )
        conn.close()

    resp = client.get("/app/chat/api/rooms/!dm:server/messages")
    assert resp.status_code == 200
    data = resp.get_json()
    by_id = {m["event_id"]: m for m in data["messages"]}
    assert by_id["$own1"]["status"] == "read"
    assert by_id["$own2"]["status"] == "delivered"
    assert data["receipts"] == [
        {
            "user_id": "@peer:server",
            "receipt_type": "m.read",
            "event_id": "$own1",
            "ts": 1500,
        }
    ]


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
    data = resp.get_json()
    assert "rooms" in data
    # identity feeds window.Chat.state.identity in the browser bootstrap;
    # without it own-message actions and ticks never render (U25.18).
    assert data["identity"]["matrix_user_id"] == OWN_ID
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


def test_peers_search_own_domain(app, seeded_client):
    client, _mock, _path, _user_id = seeded_client
    domain_id = _add_peer_account(app, "test4@test.localhost")
    resp = client.get("/app/chat/api/peers?q=test4")
    assert resp.status_code == 200
    peers = resp.get_json()["peers"]
    # Expected keys: email, username, domain_id.
    assert peers == [
        {
            "email": "test4@test.localhost",
            "username": "test4",
            "domain_id": domain_id,
        }
    ]

    resp = client.get("/app/chat/api/peers?q=t")
    assert resp.status_code == 200
    assert resp.get_json()["peers"] == []


def test_peers_search_excludes_self_and_matches_substring(app, seeded_client):
    client, _mock, _path, _user_id = seeded_client
    _add_peer_account(app, "matilda@test.localhost")
    resp = client.get("/app/chat/api/peers?q=til")
    assert resp.status_code == 200
    assert [p["email"] for p in resp.get_json()["peers"]] == ["matilda@test.localhost"]


def test_peers_search_hides_unlisted_domains(app, seeded_client):
    """Default visibility: only own-domain accounts are discoverable (U25.17)."""
    client, _mock, _path, _user_id = seeded_client
    with app.app_context():
        from app.shared.db import db

        other = Domain()
        other.name = "other.test"
        other.is_active = True
        other.status = "complete"
        other.imap_host = "imap.other.test"
        other.imap_port = 993
        other.smtp_host = "smtp.other.test"
        other.smtp_port = 587
        other.smtp_tls_mode = "starttls"
        other.matrix_host = "synapse"
        other.matrix_port = 8008
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    with app.app_context():
        from app.shared.db import db

        _add_peer_account(app, "stranger@other.test", domain=db.session.get(Domain, other_id))

    resp = client.get("/app/chat/api/peers?q=stranger")
    assert resp.status_code == 200
    assert resp.get_json()["peers"] == []

    resp = client.post("/app/chat/api/dm", json={"email": "stranger@other.test"})
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "CHAT_NOT_VISIBLE"


def test_peers_search_lists_allowlisted_domains(app, seeded_client):
    client, mock, _path, _user_id = seeded_client
    with app.app_context():
        from app.shared.db import db

        other = Domain()
        other.name = "other.test"
        other.is_active = True
        other.status = "complete"
        other.imap_host = "imap.other.test"
        other.imap_port = 993
        other.smtp_host = "smtp.other.test"
        other.smtp_port = 587
        other.smtp_tls_mode = "starttls"
        other.matrix_host = "synapse"
        other.matrix_port = 8008
        db.session.add(other)
        db.session.flush()
        main = Domain.query.first()
        assert main is not None
        main.chat_visible_domain_ids = [other.id]
        db.session.commit()
        other_id = other.id
    with app.app_context():
        from app.shared.db import db

        _add_peer_account(app, "partner@other.test", domain=db.session.get(Domain, other_id))

    resp = client.get("/app/chat/api/peers?q=partner")
    assert resp.status_code == 200
    assert [p["email"] for p in resp.get_json()["peers"]] == ["partner@other.test"]

    mock.get_displayname.return_value = "partner"
    with patch(
        "app.modules.chat.controllers.api.ensure_matrix_user",
        return_value={"matrix_user_id": "@partner:locoroo.test", "created": False},
    ):
        resp = client.post("/app/chat/api/dm", json={"email": "partner@other.test"})
    assert resp.status_code == 201
    assert resp.get_json()["peer"]["matrix_user_id"] == "@partner:locoroo.test"


def test_dm_blocked_for_allowlisted_domain_on_other_homeserver(app, seeded_client):
    client, mock, _path, _user_id = seeded_client
    with app.app_context():
        from app.shared.db import db

        other = Domain()
        other.name = "other.test"
        other.is_active = True
        other.status = "complete"
        other.imap_host = "imap.other.test"
        other.imap_port = 993
        other.smtp_host = "smtp.other.test"
        other.smtp_port = 587
        other.smtp_tls_mode = "starttls"
        other.matrix_host = "elsewhere"
        other.matrix_port = 8008
        db.session.add(other)
        db.session.flush()
        main = Domain.query.first()
        assert main is not None
        main.chat_visible_domain_ids = [other.id]
        db.session.commit()
        other_id = other.id
    with app.app_context():
        from app.shared.db import db

        _add_peer_account(app, "far@other.test", domain=db.session.get(Domain, other_id))

    resp = client.get("/app/chat/api/peers?q=far")
    assert resp.status_code == 200
    assert resp.get_json()["peers"] == []

    resp = client.post("/app/chat/api/dm", json={"email": "far@other.test"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "CHAT_CROSS_SERVER"
    mock.create_room.assert_not_called()


def test_state_unconfigured_domain_returns_503(app, authed_client):
    client, _user_id, _account_id = authed_client
    resp = client.get("/app/chat/api/state")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "MATRIX_NOT_CONFIGURED"
    assert "Admin" in resp.get_json()["error"]["message"]


def _add_peer_account(app, email, domain=None):
    from sqlalchemy import insert

    from app.shared.db import db
    from app.shared.models.core import CustomerAccount, Domain, User

    with app.app_context():
        target_domain = domain or Domain.query.first()
        assert target_domain is not None
        db.session.execute(insert(User).values(role="customer", email=email))
        peer = User.query.filter_by(email=email).first()
        assert peer is not None
        db.session.execute(
            insert(CustomerAccount).values(
                customer_id=peer.id,
                domain_id=target_domain.id,
                email_address=email,
                username=email.split("@", 1)[0],
            )
        )
        db.session.commit()
        return target_domain.id


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
