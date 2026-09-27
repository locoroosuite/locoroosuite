"""1:1 call feature tests (HLD U25.19-U25.23).

Covers: TURN credential minting, m.call.* sync ingestion, call summary
derivation, and the call signaling endpoints (happy paths + validation +
auth + error mapping).
"""

import base64
import hashlib
import hmac
import json
import time

from app.modules.chat.services import cache_db
from app.modules.chat.services.matrix import MatrixError
from app.modules.chat.services.sync import process_backfill, process_sync
from app.modules.chat.services.turn import ice_servers_for_domain
from tests.chat.conftest import OWN_ID

OWN = OWN_ID
PEER = "@peer:server"


def _call_event(event_id, event_type, call_id, sender, ts, content=None):
    return {
        "event_id": event_id,
        "type": event_type,
        "sender": sender,
        "origin_server_ts": ts,
        "content": {"call_id": call_id, "version": "1", **(content or {})},
    }


def _invite_content(sdp="v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"):
    return {"offer": {"type": "offer", "sdp": sdp}, "lifetime": 60000}


# --- TURN service --------------------------------------------------------


class _FakeDomain:
    turn_host: str | None = None
    turn_port: int | None = None
    turn_tls_port: int | None = None
    turn_shared_secret: str | None = None


def test_turn_disabled_when_unconfigured():
    payload = ice_servers_for_domain(_FakeDomain(), "user@dom")
    assert payload == {"enabled": False, "iceServers": []}


def test_turn_ice_servers_and_credential():
    domain = _FakeDomain()
    domain.turn_host = "turn.example.com"
    domain.turn_port = 3478
    domain.turn_tls_port = 5349
    domain.turn_shared_secret = "topsecret"
    payload = ice_servers_for_domain(domain, "user@dom", ttl_s=600)

    assert payload["enabled"] is True
    assert payload["ttl"] == 600
    (server,) = payload["iceServers"]
    assert server["urls"] == [
        "stun:turn.example.com:3478",
        "turn:turn.example.com:3478?transport=udp",
        "turn:turn.example.com:3478?transport=tcp",
        "turns:turn.example.com:5349?transport=tcp",
    ]
    expiry, _, username = server["username"].partition(":")
    assert username == "user@dom"
    assert int(expiry) >= int(time.time()) + 599
    expected = base64.b64encode(
        hmac.new(b"topsecret", server["username"].encode(), hashlib.sha1).digest()
    ).decode()
    assert server["credential"] == expected


def test_turn_hidden_tls_port_when_unset():
    domain = _FakeDomain()
    domain.turn_host = "turn.example.com"
    domain.turn_shared_secret = "s"
    payload = ice_servers_for_domain(domain, "u")
    (server,) = payload["iceServers"]
    assert not any(url.startswith("turns:") for url in server["urls"])


def test_turn_requires_secret_too():
    domain = _FakeDomain()
    domain.turn_host = "turn.example.com"
    payload = ice_servers_for_domain(domain, "u")
    assert payload["enabled"] is False


# --- sync ingestion ------------------------------------------------------


def test_process_sync_persists_and_relays_call_events(chat_cache):
    resp = {
        "next_batch": "s2",
        "rooms": {
            "join": {
                "!dm:server": {
                    "timeline": {
                        "events": [
                            _call_event(
                                "$inv1", "m.call.invite", "call1", PEER, 1000, _invite_content()
                            ),
                            _call_event(
                                "$cand1",
                                "m.call.candidates",
                                "call1",
                                PEER,
                                1100,
                                {"candidates": [{"candidate": "x", "sdpMid": "0"}]},
                            ),
                            _call_event(
                                "$ans1",
                                "m.call.answer",
                                "call1",
                                OWN,
                                1200,
                                {"answer": {"type": "answer", "sdp": "v=0"}},
                            ),
                            _call_event(
                                "$hup1", "m.call.hangup", "call1", PEER, 6200, {"reason": "user_hangup"}
                            ),
                        ]
                    }
                }
            }
        },
    }
    changes = process_sync(chat_cache, OWN, resp)

    # SSE payload: full signaling content, ordered.
    events = changes["calls"]["!dm:server"]
    assert [e["type"] for e in events] == [
        "m.call.invite",
        "m.call.candidates",
        "m.call.answer",
        "m.call.hangup",
    ]
    assert events[0]["content"]["offer"]["sdp"].startswith("v=0")
    assert events[0]["call_id"] == "call1"
    assert events[0]["sender"] == PEER

    # Cache: derived timeline entry — answered, hung up, 5s duration.
    (summary,) = cache_db.call_summaries(chat_cache, "!dm:server")
    assert summary["status"] == "ended"
    assert summary["duration_s"] == 5
    assert summary["sender"] == PEER
    assert summary["video"] is False
    assert summary["hangup_reason"] == "user_hangup"


def test_call_summaries_missed_and_declined_and_video(chat_cache):
    cache_db.insert_call_event(
        chat_cache,
        event_id="$i1",
        room_id="!dm:server",
        sender=OWN,
        call_id="missed",
        event_type="m.call.invite",
        content_json=json.dumps({"call_id": "missed", "offer": {"sdp": "m=audio"}}),
        origin_server_ts=1000,
    )
    cache_db.insert_call_event(
        chat_cache,
        event_id="$h1",
        room_id="!dm:server",
        sender=OWN,
        call_id="missed",
        event_type="m.call.hangup",
        content_json=json.dumps({"call_id": "missed"}),
        origin_server_ts=4000,
    )
    cache_db.insert_call_event(
        chat_cache,
        event_id="$i2",
        room_id="!dm:server",
        sender=PEER,
        call_id="declined",
        event_type="m.call.invite",
        content_json=json.dumps({"call_id": "declined", "offer": {"sdp": "m=video"}}),
        origin_server_ts=5000,
    )
    cache_db.insert_call_event(
        chat_cache,
        event_id="$h2",
        room_id="!dm:server",
        sender=OWN,
        call_id="declined",
        event_type="m.call.hangup",
        content_json=json.dumps({"call_id": "declined"}),
        origin_server_ts=6000,
    )
    summaries = {s["call_id"]: s for s in cache_db.call_summaries(chat_cache, "!dm:server")}
    # Caller hung up without an answer -> missed (from the callee's view).
    assert summaries["missed"]["status"] == "missed"
    assert summaries["missed"]["duration_s"] is None
    # Callee hung up before answering -> declined; video detected from SDP.
    assert summaries["declined"]["status"] == "declined"
    assert summaries["declined"]["video"] is True


def test_call_summaries_ringing_and_active(chat_cache):
    cache_db.insert_call_event(
        chat_cache,
        event_id="$i3",
        room_id="!dm:server",
        sender=PEER,
        call_id="ringing",
        event_type="m.call.invite",
        content_json=json.dumps({"call_id": "ringing", "offer": {"sdp": "m=audio"}}),
        origin_server_ts=1000,
    )
    cache_db.insert_call_event(
        chat_cache,
        event_id="$i4",
        room_id="!dm:server",
        sender=PEER,
        call_id="active",
        event_type="m.call.invite",
        content_json=json.dumps({"call_id": "active", "offer": {"sdp": "m=audio"}}),
        origin_server_ts=2000,
    )
    cache_db.insert_call_event(
        chat_cache,
        event_id="$a4",
        room_id="!dm:server",
        sender=OWN,
        call_id="active",
        event_type="m.call.answer",
        content_json=json.dumps({"call_id": "active"}),
        origin_server_ts=3000,
    )
    summaries = {s["call_id"]: s for s in cache_db.call_summaries(chat_cache, "!dm:server")}
    assert summaries["ringing"]["status"] == "ringing"
    assert summaries["active"]["status"] == "active"
    assert summaries["active"]["duration_s"] is None


def test_call_exists_and_delete_room_cleans_call_events(chat_cache):
    cache_db.upsert_room(chat_cache, "!dm:server", is_direct=True)
    cache_db.insert_call_event(
        chat_cache,
        event_id="$i5",
        room_id="!dm:server",
        sender=PEER,
        call_id="c1",
        event_type="m.call.invite",
        content_json=json.dumps({"call_id": "c1"}),
        origin_server_ts=1000,
    )
    assert cache_db.call_exists(chat_cache, "!dm:server", "c1") is True
    assert cache_db.call_exists(chat_cache, "!dm:server", "other") is False
    cache_db.delete_room(chat_cache, "!dm:server")
    assert cache_db.call_exists(chat_cache, "!dm:server", "c1") is False
    assert cache_db.call_summaries(chat_cache, "!dm:server") == []


def test_process_backfill_persists_call_events_without_rows(chat_cache):
    cache_db.upsert_room(chat_cache, "!dm:server", prev_batch="t0")
    resp = {
        "start": "t0",
        "end": "t1",
        "chunk": [
            _call_event("$binv", "m.call.invite", "oldcall", PEER, 500, _invite_content()),
            {
                "event_id": "$bmsg",
                "type": "m.room.message",
                "sender": PEER,
                "origin_server_ts": 600,
                "content": {"msgtype": "m.text", "body": "hi"},
            },
        ],
    }
    rows = process_backfill(chat_cache, OWN, "!dm:server", resp)
    # Only message rows are returned; call events are persisted silently.
    assert [r["event_id"] for r in rows] == ["$bmsg"]
    summaries = cache_db.call_summaries(chat_cache, "!dm:server")
    assert [s["call_id"] for s in summaries] == ["oldcall"]
    assert summaries[0]["status"] == "ringing"


# --- TURN endpoint -------------------------------------------------------


def test_turn_endpoint_unconfigured(seeded_client, app):
    client, _mock, _path, _user_id = seeded_client
    resp = client.get("/app/chat/api/turn")
    assert resp.status_code == 200
    assert resp.get_json() == {"enabled": False, "iceServers": []}


def test_turn_endpoint_configured(seeded_client, app):
    client, _mock, _path, _user_id = seeded_client
    from app.shared.db import db
    from app.shared.models.core import Domain

    with app.app_context():
        domain = Domain.query.first()
        assert domain is not None
        domain.turn_host = "localhost"
        domain.turn_shared_secret = "dev-turn-secret"
        db.session.commit()

    resp = client.get("/app/chat/api/turn")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True
    (server,) = data["iceServers"]
    assert any("turn:localhost:3478" in url for url in server["urls"])


def test_turn_endpoint_requires_login(client):
    resp = client.get("/app/chat/api/turn")
    assert resp.status_code == 302


# --- call endpoints ------------------------------------------------------


def _matrix_error(exc):
    raise exc


def test_start_call_happy(seeded_client):
    client, mock, _path, _user_id = seeded_client
    mock.send_event.return_value = {"event_id": "$inv1"}
    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call",
        json={"video": False, "offer": {"type": "offer", "sdp": "v=0\r\nm=audio 9"}},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["event_id"] == "$inv1"
    assert data["call_id"]

    room_id, event_type, content = mock.send_event.call_args[0][:3]
    assert room_id == "!dm:server"
    assert event_type == "m.call.invite"
    assert content["call_id"] == data["call_id"]
    assert content["offer"]["sdp"].startswith("v=0")
    assert content["version"] == "1"
    assert content["lifetime"] == 60000


def test_start_call_validation(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    # No offer.
    resp = client.post("/app/chat/api/rooms/!dm:server/call", json={"video": True})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "VALIDATION"
    # offer.sdp not a string.
    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call", json={"offer": {"type": "offer", "sdp": 7}}
    )
    assert resp.status_code == 400
    # Empty body.
    resp = client.post("/app/chat/api/rooms/!dm:server/call")
    assert resp.status_code == 400


def test_start_call_group_room_rejected(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.post(
        "/app/chat/api/rooms/!room1:server/call",
        json={"offer": {"type": "offer", "sdp": "v=0"}},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "CALLS_DM_ONLY"


def test_start_call_room_not_found(seeded_client):
    client, _mock, _path, _user_id = seeded_client
    resp = client.post(
        "/app/chat/api/rooms/!missing:server/call", json={"offer": {"type": "offer", "sdp": "v=0"}}
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "ROOM_NOT_FOUND"


def test_start_call_requires_login(client):
    resp = client.post("/app/chat/api/rooms/!dm:server/call", json={})
    assert resp.status_code == 302


def test_answer_call_unknown_call(seeded_client):
    client, mock, _path, _user_id = seeded_client
    mock.send_event.return_value = {"event_id": "$ev"}
    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call/cunknown/answer",
        json={"answer": {"type": "answer", "sdp": "v=0"}},
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "CALL_NOT_FOUND"


def test_answer_and_candidates_and_hangup_happy(seeded_client, app):
    client, mock, path, user_id = seeded_client
    from app.shared.keys import get_user_key

    with app.app_context():
        conn = cache_db.open_cache(path, get_user_key(user_id))
        cache_db.insert_call_event(
            conn,
            event_id="$inv",
            room_id="!dm:server",
            sender=PEER,
            call_id="c1",
            event_type="m.call.invite",
            content_json=json.dumps({"call_id": "c1"}),
            origin_server_ts=1000,
        )
        conn.close()

    mock.send_event.return_value = {"event_id": "$ev"}
    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call/c1/answer",
        json={"answer": {"type": "answer", "sdp": "v=0\r\na=recvonly"}},
    )
    assert resp.status_code == 201
    _room_id, event_type, content = mock.send_event.call_args[0][:3]
    assert event_type == "m.call.answer"
    assert content["call_id"] == "c1"
    assert content["answer"]["sdp"].startswith("v=0")

    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call/c1/candidates",
        json={"candidates": [{"candidate": "abc", "sdpMid": "0"}]},
    )
    assert resp.status_code == 201
    _room_id, event_type, content = mock.send_event.call_args[0][:3]
    assert event_type == "m.call.candidates"
    assert content["candidates"] == [{"candidate": "abc", "sdpMid": "0"}]

    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call/c1/hangup", json={"reason": "user_hangup"}
    )
    assert resp.status_code == 201
    _room_id, event_type, content = mock.send_event.call_args[0][:3]
    assert event_type == "m.call.hangup"
    assert content["reason"] == "user_hangup"


def test_candidates_validation(seeded_client, app):
    client, _mock, path, user_id = seeded_client
    from app.shared.keys import get_user_key

    with app.app_context():
        conn = cache_db.open_cache(path, get_user_key(user_id))
        cache_db.insert_call_event(
            conn,
            event_id="$inv2",
            room_id="!dm:server",
            sender=PEER,
            call_id="c2",
            event_type="m.call.invite",
            content_json=json.dumps({"call_id": "c2"}),
            origin_server_ts=1000,
        )
        conn.close()

    resp = client.post("/app/chat/api/rooms/!dm:server/call/c2/candidates", json={})
    assert resp.status_code == 400
    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call/c2/candidates", json={"candidates": "not-a-list"}
    )
    assert resp.status_code == 400
    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call/c2/candidates", json={"candidates": ["str"]}
    )
    assert resp.status_code == 400


def test_hangup_rejects_unknown_reason(seeded_client, app):
    client, _mock, path, user_id = seeded_client
    from app.shared.keys import get_user_key

    with app.app_context():
        conn = cache_db.open_cache(path, get_user_key(user_id))
        cache_db.insert_call_event(
            conn,
            event_id="$inv3",
            room_id="!dm:server",
            sender=PEER,
            call_id="c3",
            event_type="m.call.invite",
            content_json=json.dumps({"call_id": "c3"}),
            origin_server_ts=1000,
        )
        conn.close()

    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call/c3/hangup", json={"reason": "bogus_reason"}
    )
    assert resp.status_code == 400


def test_call_endpoints_map_matrix_errors(seeded_client):
    client, mock, _path, _user_id = seeded_client
    mock.send_event.side_effect = MatrixError("M_FORBIDDEN", "nope", status=403)
    resp = client.post(
        "/app/chat/api/rooms/!dm:server/call", json={"offer": {"type": "offer", "sdp": "v=0"}}
    )
    assert resp.status_code == 502
    assert resp.get_json()["error"]["code"] == "M_FORBIDDEN"


def test_room_messages_includes_calls(seeded_client, app):
    client, _mock, path, user_id = seeded_client
    from app.shared.keys import get_user_key

    with app.app_context():
        conn = cache_db.open_cache(path, get_user_key(user_id))
        cache_db.insert_call_event(
            conn,
            event_id="$inv4",
            room_id="!dm:server",
            sender=PEER,
            call_id="c4",
            event_type="m.call.invite",
            content_json=json.dumps({"call_id": "c4", "offer": {"sdp": "m=audio"}}),
            origin_server_ts=1000,
        )
        conn.close()

    resp = client.get("/app/chat/api/rooms/!dm:server/messages")
    assert resp.status_code == 200
    calls = resp.get_json()["calls"]
    assert [c["call_id"] for c in calls] == ["c4"]
    assert calls[0]["status"] == "ringing"
