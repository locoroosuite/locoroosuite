from app.modules.chat.services import cache_db


def test_open_cache_creates_schema(chat_cache):
    tables = {
        row["name"]
        for row in chat_cache.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "chat_credentials",
        "chat_rooms",
        "chat_room_members",
        "chat_messages",
        "chat_read_state",
        "chat_sync_state",
    } <= tables


def test_room_message_reaction_roundtrip(chat_cache):
    cache_db.upsert_room(chat_cache, "!r1:server", name="General", topic="Talk", is_public=True)
    cache_db.upsert_member(chat_cache, "!r1:server", "@alice:server", displayname="Alice")
    cache_db.upsert_member(chat_cache, "!r1:server", "@bob:server", displayname="Bob")

    cache_db.insert_message(
        chat_cache,
        event_id="$e1",
        room_id="!r1:server",
        sender="@alice:server",
        type="m.room.message",
        body="first",
        content_json='{"msgtype":"m.text","body":"first"}',
        origin_server_ts=1000,
    )
    cache_db.insert_message(
        chat_cache,
        event_id="$e2",
        room_id="!r1:server",
        sender="@bob:server",
        type="m.room.message",
        body="second",
        content_json='{"msgtype":"m.text","body":"second"}',
        origin_server_ts=2000,
    )
    cache_db.insert_message(
        chat_cache,
        event_id="$react1",
        room_id="!r1:server",
        sender="@bob:server",
        type="m.reaction",
        body="👍",
        content_json="{}",
        relates_to="$e1",
        rel_type="m.annotation",
        origin_server_ts=2500,
    )
    chat_cache.commit()

    messages = cache_db.list_messages(chat_cache, "!r1:server")
    assert [m["event_id"] for m in messages] == ["$e1", "$e2"]

    reactions = cache_db.list_reactions(chat_cache, "!r1:server")
    assert reactions["$e1"] == [{"key": "👍", "sender": "@bob:server"}]

    api_msg = cache_db.message_to_api(messages[0], reactions.get("$e1"), own_user_id="@bob:server")
    assert api_msg["event_id"] == "$e1"
    assert api_msg["reactions"] == [{"key": "👍", "count": 1, "mine": True}]


def test_dm_display_name(chat_cache):
    cache_db.upsert_room(chat_cache, "!dm1:server", is_direct=True)
    cache_db.upsert_member(chat_cache, "!dm1:server", "@me:server", displayname="Me")
    cache_db.upsert_member(chat_cache, "!dm1:server", "@peer:server", displayname="Peer Name")
    chat_cache.commit()

    rooms = cache_db.list_rooms(chat_cache, "@me:server")
    assert rooms[0]["display_name"] == "Peer Name"
    assert cache_db.count_joined_members(chat_cache, "!dm1:server") == 2


def test_redact_and_edit(chat_cache):
    cache_db.upsert_room(chat_cache, "!r1:server", name="R")
    cache_db.insert_message(
        chat_cache,
        event_id="$e1",
        room_id="!r1:server",
        sender="@me:server",
        type="m.room.message",
        body="original",
        content_json='{"msgtype":"m.text","body":"original"}',
        origin_server_ts=1000,
    )
    cache_db.update_message_content(
        chat_cache, "$e1", '{"msgtype":"m.text","body":"edited"}', "edited"
    )
    row = cache_db.get_message(chat_cache, "$e1")
    assert row is not None
    assert row["edited"] == 1
    assert row["body"] == "edited"

    cache_db.mark_redacted(chat_cache, "$e1")
    row = cache_db.get_message(chat_cache, "$e1")
    assert row is not None
    assert row["redacted"] == 1
    assert row["body"] is None


def test_read_state_and_sync_state(chat_cache):
    cache_db.set_read_state(chat_cache, "!r:server", read_event_id="$a", fully_read_event_id="$a")
    state = cache_db.get_read_state(chat_cache, "!r:server")
    assert state is not None
    assert state["read_event_id"] == "$a"

    cache_db.set_sync_state(chat_cache, "s1_123")
    sync = cache_db.get_sync_state(chat_cache)
    assert sync is not None
    assert sync["since_token"] == "s1_123"
