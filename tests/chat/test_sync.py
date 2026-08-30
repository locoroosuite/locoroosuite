from app.modules.chat.services import cache_db
from app.modules.chat.services.sync import process_backfill, process_sync

OWN = "@me:server"


def _sync_response_join():
    return {
        "next_batch": "s2",
        "rooms": {
            "join": {
                "!room1:server": {
                    "state": {
                        "events": [
                            {
                                "type": "m.room.name",
                                "state_key": "",
                                "content": {"name": "General"},
                            },
                            {"type": "m.room.topic", "state_key": "", "content": {"topic": "Work"}},
                            {
                                "type": "m.room.member",
                                "state_key": "@me:server",
                                "content": {"membership": "join", "is_direct": True},
                            },
                            {
                                "type": "m.room.member",
                                "state_key": "@peer:server",
                                "content": {"membership": "join", "displayname": "Peer"},
                            },
                        ]
                    },
                    "timeline": {
                        "prev_batch": "t0",
                        "events": [
                            {
                                "event_id": "$m1",
                                "type": "m.room.message",
                                "sender": "@peer:server",
                                "origin_server_ts": 1000,
                                "content": {"msgtype": "m.text", "body": "hello"},
                            },
                            {
                                "event_id": "$m2",
                                "type": "m.room.message",
                                "sender": "@me:server",
                                "origin_server_ts": 2000,
                                "content": {"msgtype": "m.text", "body": "typo"},
                            },
                            {
                                "event_id": "$edit1",
                                "type": "m.room.message",
                                "sender": "@me:server",
                                "origin_server_ts": 3000,
                                "content": {
                                    "msgtype": "m.text",
                                    "body": "* fixed",
                                    "m.new_content": {"msgtype": "m.text", "body": "fixed"},
                                    "m.relates_to": {"rel_type": "m.replace", "event_id": "$m2"},
                                },
                            },
                            {
                                "event_id": "$react1",
                                "type": "m.reaction",
                                "sender": "@peer:server",
                                "origin_server_ts": 3500,
                                "content": {
                                    "m.relates_to": {
                                        "rel_type": "m.annotation",
                                        "event_id": "$m1",
                                        "key": "🎉",
                                    }
                                },
                            },
                            {
                                "event_id": "$redact1",
                                "type": "m.room.redaction",
                                "sender": "@me:server",
                                "origin_server_ts": 4000,
                                "redacts": "$m1",
                                "content": {},
                            },
                        ],
                    },
                    "ephemeral": {
                        "events": [{"type": "m.typing", "content": {"user_ids": ["@peer:server"]}}]
                    },
                    "unread_notifications": {"notification_count": 3, "highlight_count": 1},
                    "summary": {"m.joined_member_count": 2},
                    "account_data": {
                        "events": [{"type": "m.fully_read", "content": {"event_id": "$redact1"}}]
                    },
                }
            },
            "invite": {
                "!inv:server": {
                    "invite_state": {
                        "events": [
                            {
                                "type": "m.room.member",
                                "state_key": "@me:server",
                                "content": {"membership": "invite"},
                            },
                            {
                                "type": "m.room.name",
                                "state_key": "",
                                "content": {"name": "Project X"},
                            },
                        ]
                    }
                }
            },
            "leave": {"!old:server": {}},
        },
    }


def test_process_sync_full_join_cycle(chat_cache):
    changes = process_sync(chat_cache, OWN, _sync_response_join())

    assert "!room1:server" in changes["rooms_upserted"]
    assert "!inv:server" in changes["rooms_upserted"]
    assert "!old:server" in changes["rooms_removed"]
    assert changes["typing"]["!room1:server"] == ["@peer:server"]

    room = cache_db.get_room(chat_cache, "!room1:server")
    assert room is not None
    assert room["name"] == "General"
    assert room["topic"] == "Work"
    assert room["is_direct"] == 1
    assert room["notification_count"] == 3
    assert room["highlight_count"] == 1
    assert room["member_count"] == 2
    assert room["prev_batch"] == "t0"

    edited = cache_db.get_message(chat_cache, "$m2")
    assert edited is not None
    assert edited["body"] == "fixed"
    assert edited["edited"] == 1

    redacted = cache_db.get_message(chat_cache, "$m1")
    assert redacted is not None
    assert redacted["redacted"] == 1

    reactions = cache_db.list_reactions(chat_cache, "!room1:server")
    assert reactions["$m1"][0]["key"] == "🎉"

    read = cache_db.get_read_state(chat_cache, "!room1:server")
    assert read is not None
    assert read["fully_read_event_id"] == "$redact1"

    sync_state = cache_db.get_sync_state(chat_cache)
    assert sync_state is not None
    assert sync_state["since_token"] == "s2"

    invite_room = cache_db.get_room(chat_cache, "!inv:server")
    assert invite_room is not None
    assert invite_room["membership"] == "invite"
    assert invite_room["name"] == "Project X"

    rooms = cache_db.list_rooms(chat_cache, OWN)
    ids = {r["room_id"]: r for r in rooms}
    assert "!room1:server" in ids
    assert "!inv:server" in ids
    assert "!old:server" not in ids
    assert ids["!room1:server"]["display_name"] == "General"


def test_process_sync_dm_display_name_from_member(chat_cache):
    resp = {
        "next_batch": "s3",
        "rooms": {
            "join": {
                "!dm:server": {
                    "state": {
                        "events": [
                            {
                                "type": "m.room.member",
                                "state_key": "@me:server",
                                "content": {"membership": "join", "is_direct": True},
                            },
                            {
                                "type": "m.room.member",
                                "state_key": "@peer:server",
                                "content": {"membership": "join", "displayname": "Alice Peer"},
                            },
                        ]
                    },
                    "timeline": {"events": []},
                }
            }
        },
    }
    process_sync(chat_cache, OWN, resp)
    rooms = cache_db.list_rooms(chat_cache, OWN)
    dm = next(r for r in rooms if r["room_id"] == "!dm:server")
    assert dm["display_name"] == "Alice Peer"
    assert dm["is_direct"] == 1


def test_process_backfill_applies_history_and_token(chat_cache):
    cache_db.upsert_room(chat_cache, "!r:server", name="R", prev_batch="t5")
    resp = {
        "start": "t5",
        "end": "t9",
        "chunk": [
            {
                "event_id": "$old1",
                "type": "m.room.message",
                "sender": "@peer:server",
                "origin_server_ts": 500,
                "content": {"msgtype": "m.text", "body": "ancient"},
            },
            {
                "event_id": "$old2",
                "type": "m.room.message",
                "sender": "@peer:server",
                "origin_server_ts": 600,
                "content": {"msgtype": "m.text", "body": "older"},
            },
        ],
    }
    rows = process_backfill(chat_cache, OWN, "!r:server", resp)
    assert len(rows) == 2
    room = cache_db.get_room(chat_cache, "!r:server")
    assert room is not None
    assert room["prev_batch"] == "t9"
    messages = cache_db.list_messages(chat_cache, "!r:server")
    assert [m["body"] for m in messages] == ["ancient", "older"]
    assert {r["event_id"] for r in rows} == {"$old1", "$old2"}
