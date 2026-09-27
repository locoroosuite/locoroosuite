"""Delivery/read receipts unit tests (HLD U25.18).

Covers: m.receipt parsing, app-internal delivery recording (DM gating,
dedup, marker deltas), and per-message status computation.
"""

import json
from datetime import UTC, datetime, timedelta

from app.modules.chat.services import cache_db, receipts

OWN = "@me:server"
PEER = "@peer:server"


def _seed_dm(conn, room_id="!dm:server", is_direct=True):
    cache_db.upsert_room(conn, room_id, is_direct=is_direct, name="Room")
    cache_db.upsert_member(conn, room_id, OWN)
    cache_db.upsert_member(conn, room_id, PEER, displayname="Peer")


def _msg(conn, event_id, ts, sender=OWN, room_id="!dm:server", body="x"):
    cache_db.insert_message(
        conn,
        event_id=event_id,
        room_id=room_id,
        sender=sender,
        type="m.room.message",
        body=body,
        content_json=json.dumps({"msgtype": "m.text", "body": body}),
        origin_server_ts=ts,
    )


def _api_msgs(conn, room_id="!dm:server"):
    rows = cache_db.list_messages(conn, room_id)
    return [cache_db.message_to_api(r, reactions=None, own_user_id=OWN) for r in rows]


def _receipt_event(event_id, user_id=PEER, ts=5000):
    return {
        "type": "m.receipt",
        "content": {event_id: {"m.read": {user_id: {"ts": ts}}}},
    }


# --- extraction -----------------------------------------------------------


def test_extract_read_receipts_parses_and_filters():
    events = [
        {"type": "m.typing", "content": {"user_ids": [PEER]}},
        {
            "type": "m.receipt",
            "content": {
                "$a": {"m.read": {PEER: {"ts": 1000}, OWN: {"ts": 999}}},
                "$b": {"m.read.private": {PEER: {"ts": 2000}}},
            },
        },
    ]
    got = receipts.extract_read_receipts(events, OWN)
    # own receipt dropped, private receipts ignored (not distributed), typing skipped
    assert got == [{"user_id": PEER, "event_id": "$a", "ts": 1000}]


def test_extract_read_receipts_tolerates_missing_ts():
    events = [{"type": "m.receipt", "content": {"$a": {"m.read": {PEER: {}}}}}]
    assert receipts.extract_read_receipts(events, OWN) == [
        {"user_id": PEER, "event_id": "$a", "ts": 0}
    ]


# --- delivery recording (recipient side) ----------------------------------


def test_record_deliveries_dm_foreign_messages(app, chat_cache):
    _seed_dm(chat_cache)
    changes = {"messages": {"!dm:server": [{"event_id": "$x1", "sender": PEER}]}}
    with app.app_context():
        assert receipts.record_deliveries(chat_cache, OWN, changes) == 1
        # Idempotent: same rows again record nothing new.
        assert receipts.record_deliveries(chat_cache, OWN, changes) == 0


def test_record_deliveries_skips_own_and_group_messages(app, chat_cache):
    _seed_dm(chat_cache)
    _seed_dm(chat_cache, "!group:server", is_direct=False)
    changes = {
        "messages": {
            "!dm:server": [{"event_id": "$own", "sender": OWN}],
            "!group:server": [{"event_id": "$grp", "sender": PEER}],
        }
    }
    with app.app_context():
        assert receipts.record_deliveries(chat_cache, OWN, changes) == 0


def test_delivered_event_ids_filters_by_sender(app, chat_cache):
    _seed_dm(chat_cache)
    with app.app_context():
        receipts.record_deliveries(
            chat_cache,
            OWN,
            {"messages": {"!dm:server": [{"event_id": "$x1", "sender": PEER}]}},
        )
        got = receipts.delivered_event_ids("!dm:server", PEER, ["$x1", "$nope"])
    assert got == {"$x1"}


def test_new_deliveries_for_sender_marker_advances(app, chat_cache):
    _seed_dm(chat_cache)
    with app.app_context():
        receipts.record_deliveries(
            chat_cache,
            OWN,
            {"messages": {"!dm:server": [{"event_id": "$x1", "sender": PEER}]}},
        )
        marker = datetime.now(UTC) + timedelta(seconds=1)
        # Nothing newer than a future marker.
        assert receipts.new_deliveries_for_sender(PEER, marker) == ([], marker)
        # A marker in the past sees the row exactly once.
        old = datetime.now(UTC) - timedelta(minutes=5)
        payload, new_marker = receipts.new_deliveries_for_sender(PEER, old)
        assert payload == [{"room_id": "!dm:server", "event_id": "$x1"}]
        assert new_marker >= old
        # And the advanced marker does not repeat it.
        assert receipts.new_deliveries_for_sender(PEER, new_marker) == ([], new_marker)


# --- status computation ----------------------------------------------------


def test_decorate_statuses_sent_delivered_read(app, chat_cache):
    _seed_dm(chat_cache)
    _msg(chat_cache, "$a", 1000)
    _msg(chat_cache, "$b", 2000)
    _msg(chat_cache, "$c", 3000, sender=PEER, body="peer")
    messages = _api_msgs(chat_cache)

    with app.app_context():
        receipts.record_deliveries(
            chat_cache,
            OWN,
            {"messages": {"!dm:server": [{"event_id": "$c", "sender": PEER}]}},
        )
        # Peer read up to $b (ts 2000): $a and $b are read, nothing else yet.
        cache_db.upsert_receipt(chat_cache, "!dm:server", PEER, "m.read", "$b", 2500)
        room = cache_db.get_room(chat_cache, "!dm:server")
        assert room is not None
        receipts.decorate_statuses(chat_cache, room, messages, OWN)

    by_id = {m["event_id"]: m for m in messages}
    assert by_id["$a"]["status"] == "read"
    assert by_id["$b"]["status"] == "read"
    assert by_id["$c"]["status"] is None  # peer's own message: no status


def test_decorate_statuses_delivered_without_receipt(app, chat_cache):
    _seed_dm(chat_cache)
    _msg(chat_cache, "$a", 1000)
    messages = _api_msgs(chat_cache)
    with app.app_context():
        receipts.record_deliveries(
            chat_cache,
            OWN,
            {"messages": {"!dm:server": [{"event_id": "$a", "sender": PEER}]}},
        )
    # Simulate the sender's view: delivery row exists for THEIR message.
    with app.app_context():
        receipts.record_deliveries(
            chat_cache,
            PEER,
            {"messages": {"!dm:server": [{"event_id": "$a", "sender": OWN}]}},
        )
        room = cache_db.get_room(chat_cache, "!dm:server")
        assert room is not None
        receipts.decorate_statuses(chat_cache, room, messages, OWN)
    assert messages[0]["status"] == "delivered"


def test_decorate_statuses_sent_when_nothing_arrived(app, chat_cache):
    _seed_dm(chat_cache)
    _msg(chat_cache, "$a", 1000)
    messages = _api_msgs(chat_cache)
    room = cache_db.get_room(chat_cache, "!dm:server")
    assert room is not None
    with app.app_context():
        receipts.decorate_statuses(chat_cache, room, messages, OWN)
    assert messages[0]["status"] == "sent"


def test_decorate_statuses_receipt_for_uncached_event_falls_back_to_ts(app, chat_cache):
    _seed_dm(chat_cache)
    _msg(chat_cache, "$a", 1000)
    # Receipt points at an event outside the cache window; its ts (5000)
    # still covers $a (1000).
    cache_db.upsert_receipt(chat_cache, "!dm:server", PEER, "m.read", "$ancient", 5000)
    messages = _api_msgs(chat_cache)
    room = cache_db.get_room(chat_cache, "!dm:server")
    assert room is not None
    with app.app_context():
        receipts.decorate_statuses(chat_cache, room, messages, OWN)
    assert messages[0]["status"] == "read"


def test_decorate_statuses_group_room_gets_null(app, chat_cache):
    _seed_dm(chat_cache, "!group:server", is_direct=False)
    _msg(chat_cache, "$a", 1000, room_id="!group:server")
    messages = _api_msgs(chat_cache, "!group:server")
    room = cache_db.get_room(chat_cache, "!group:server")
    assert room is not None
    receipts.decorate_statuses(chat_cache, room, messages, OWN)
    assert messages[0]["status"] is None


def test_decorate_statuses_redacted_own_message_gets_null(app, chat_cache):
    _seed_dm(chat_cache)
    _msg(chat_cache, "$a", 1000)
    cache_db.mark_redacted(chat_cache, "$a")
    messages = _api_msgs(chat_cache)
    room = cache_db.get_room(chat_cache, "!dm:server")
    assert room is not None
    receipts.decorate_statuses(chat_cache, room, messages, OWN)
    assert messages[0]["status"] is None
