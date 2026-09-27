"""Tests for the headless chat push worker (HLD U25.61)."""

import contextlib
import os
import tempfile
from unittest.mock import patch

import pytest

from app.modules.chat.services import cache_db, push_log, streams
from app.modules.chat.services.cache import get_cache_path
from app.modules.chat.services.push_log import prune_push_log
from app.modules.chat.services.sync import process_sync
from app.shared.db import db
from app.shared.keys import get_user_key
from app.shared.models.core import (
    ChatMessageDelivery,
    CustomerAccount,
    CustomerSettings,
    Domain,
    PushSubscription,
)
from app.workers.chat_push import ChatPushWorker, plan_pushes

OWN_ID = "@tester:locoroo.test"
PEER_ID = "@peer:server"


@pytest.fixture()
def chat_cache():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = cache_db.open_cache(path, "0" * 64)
    yield conn
    conn.close()
    with contextlib.suppress(OSError):
        os.unlink(path)


SYNC_RESP = {
    "next_batch": "s2",
    "rooms": {
        "join": {
            "!dm:server": {
                "state": {"events": []},
                "timeline": {
                    "events": [
                        {
                            "type": "m.room.message",
                            "event_id": "$w1",
                            "sender": PEER_ID,
                            "origin_server_ts": 1111,
                            "content": {"msgtype": "m.text", "body": "hi"},
                        },
                        {
                            "type": "m.reaction",
                            "event_id": "$r1",
                            "sender": PEER_ID,
                            "origin_server_ts": 1112,
                            "content": {
                                "m.relates_to": {
                                    "rel_type": "m.annotation",
                                    "event_id": "$w1",
                                    "key": "👍",
                                }
                            },
                        },
                        {
                            "type": "m.call.invite",
                            "event_id": "$c1",
                            "sender": PEER_ID,
                            "origin_server_ts": 2222,
                            "content": {
                                "call_id": "call1",
                                "lifetime": 60000,
                                "lr.video": True,
                                "offer": {"type": "offer", "sdp": "v=0\r\nm=audio 9 UDP"},
                            },
                        },
                        {
                            # Own events echo back via sync: never pushed.
                            "type": "m.room.message",
                            "event_id": "$own1",
                            "sender": OWN_ID,
                            "origin_server_ts": 3333,
                            "content": {"msgtype": "m.text", "body": "me too"},
                        },
                    ]
                },
                "ephemeral": {"events": []},
                "account_data": {"events": []},
                "unread_notifications": {"notification_count": 1, "highlight_count": 0},
                "summary": {"m.joined_member_count": 2},
            }
        },
        "invite": {
            "!newroom:server": {
                "invite_state": {
                    "events": [
                        {
                            "type": "m.room.member",
                            "state_key": OWN_ID,
                            "sender": "@inviter:server",
                            "content": {"membership": "invite"},
                        }
                    ]
                }
            }
        },
    },
}


def _process(chat_cache):
    """Run one sync batch through process_sync; return its changes."""
    return process_sync(chat_cache, OWN_ID, SYNC_RESP)


class TestPlanPushes:
    def test_message_call_and_invite_intents(self, chat_cache):
        changes = _process(chat_cache)
        intents = plan_pushes(chat_cache, OWN_ID, changes, SYNC_RESP, known_invite_rooms=set())
        kinds = sorted(i["kind"] for i in intents)
        assert kinds == ["call", "invite", "message"]
        message = next(i for i in intents if i["kind"] == "message")
        assert message["room_id"] == "!dm:server"
        assert message["count"] == 1  # reaction + own echo excluded
        assert message["event_ids"] == ["$w1"]
        assert message["sender"] == PEER_ID
        call = next(i for i in intents if i["kind"] == "call")
        assert call["call_id"] == "call1"
        assert call["video"] is True  # lr.video flag on the invite
        invite = next(i for i in intents if i["kind"] == "invite")
        assert invite["room_id"] == "!newroom:server"
        assert invite["event_ids"] == ["$inv1"] or invite["sender"] == "@inviter:server"

    def test_dedup_after_marking(self, chat_cache):
        changes = _process(chat_cache)
        first = plan_pushes(chat_cache, OWN_ID, changes, SYNC_RESP, known_invite_rooms=set())
        for intent in first:
            for event_id in intent["event_ids"]:
                push_log.mark_pushed(chat_cache, event_id, intent["kind"])
        # Replay the same batch (crash between push and token commit): no re-ring.
        again = plan_pushes(chat_cache, OWN_ID, changes, SYNC_RESP, known_invite_rooms=set())
        assert again == []

    def test_known_invite_room_not_pushed(self, chat_cache):
        _process(chat_cache)
        intents = plan_pushes(
            chat_cache, OWN_ID, {"messages": {}}, SYNC_RESP, known_invite_rooms={"!newroom:server"}
        )
        assert intents == []

    def test_own_call_invite_echo_not_pushed(self, chat_cache):
        changes = {
            "calls": {
                "!dm:server": [
                    {
                        "event_id": "$c2",
                        "room_id": "!dm:server",
                        "sender": OWN_ID,
                        "type": "m.call.invite",
                        "call_id": "call2",
                        "content": {"call_id": "call2"},
                        "origin_server_ts": 9999,
                    }
                ]
            }
        }
        assert plan_pushes(chat_cache, OWN_ID, changes, {}, set()) == []


class TestPushLogPrune:
    def test_prune_removes_old_rows_only(self, chat_cache):
        push_log.mark_pushed(chat_cache, "$old", "message")
        chat_cache.execute(
            "UPDATE chat_push_log SET pushed_at = '2020-01-01T00:00:00+00:00' WHERE event_id = ?",
            ("$old",),
        )
        chat_cache.commit()
        push_log.mark_pushed(chat_cache, "$new", "message")
        prune_push_log(chat_cache)
        assert push_log.was_pushed(chat_cache, "$new")
        assert not push_log.was_pushed(chat_cache, "$old")


class TestStreamRegistry:
    def test_refcounted_register_unregister(self):
        streams.register_stream(1)
        streams.register_stream(1)
        assert streams.has_active_stream(1)
        streams.unregister_stream(1)
        assert streams.has_active_stream(1)
        streams.unregister_stream(1)
        assert not streams.has_active_stream(1)
        # Unregister below zero is a no-op, not an error.
        streams.unregister_stream(1)
        assert not streams.has_active_stream(1)


def _seed_user(app, user_id):
    settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
    if not settings:
        settings = CustomerSettings()
        settings.customer_id = user_id
        db.session.add(settings)
    settings.notify_chat_enabled = True
    row = PushSubscription()
    row.user_id = user_id
    row.endpoint = "https://push.example.com/sub/chat"
    row.p256dh = "p256dh-key"
    row.auth = "auth-key"
    row.user_agent = "pytest-agent"
    db.session.add(row)
    db.session.commit()
    return settings


def _seed_chat_cache(user_id, account):
    path = get_cache_path(account)
    conn = cache_db.open_cache(path, get_user_key(user_id))
    try:
        cache_db.set_credentials(
            conn,
            matrix_user_id=OWN_ID,
            access_token="syt_tok",
            device_id="DEV1",
            password_encrypted="enc",
            homeserver_url="http://synapse:8008",
        )
        cache_db.upsert_room(conn, "!dm:server", is_direct=True)
        cache_db.upsert_member(conn, "!dm:server", OWN_ID, displayname="Tester")
        cache_db.upsert_member(conn, "!dm:server", PEER_ID, displayname="Peer")
        conn.close()
    except Exception:
        conn.close()
        raise
    return path


class TestChatPushWorkerTick:
    def _tick(self, app):
        worker = ChatPushWorker(app)
        with patch("app.workers.chat_push.MatrixClient") as mock_client:
            mock_client.return_value.sync.return_value = SYNC_RESP
            worker.tick()
        return mock_client

    def test_sends_each_kind_once_and_dedups(self, app, authed_client):
        _client, user_id, account_id = authed_client
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            assert account is not None
            domain = db.session.get(Domain, account.domain_id)
            assert domain is not None
            domain.matrix_host = "synapse"
            domain.matrix_port = 8008
            db.session.commit()
            _seed_user(app, user_id)
            path = _seed_chat_cache(user_id, account)
            try:
                with (
                    patch("app.shared.push.send_chat_message_push") as send_msg,
                    patch("app.shared.push.send_chat_call_push") as send_call,
                    patch("app.shared.push.send_chat_invite_push") as send_invite,
                ):
                    self._tick(app)
                    assert send_msg.call_count == 1
                    assert send_call.call_count == 1
                    assert send_invite.call_count == 1
                    # Privacy default: detailed names are empty strings.
                    assert send_msg.call_args.args[4] == ""  # sender_name
                    assert send_msg.call_args.args[5] == ""  # room_name
                    assert send_call.call_args.args[3] == ""  # caller_name
                    assert send_call.call_args.args[4] is True  # video
                    # Offline delivery rows (U25.18) were recorded.
                    assert (
                        db.session.query(ChatMessageDelivery)
                        .filter_by(event_id="$w1", recipient_matrix_id=OWN_ID)
                        .count()
                        == 1
                    )
                    # Same batch replayed: deduped, nothing re-sent.
                    self._tick(app)
                    assert send_msg.call_count == 1
                    assert send_call.call_count == 1
                    assert send_invite.call_count == 1
            finally:
                if os.path.exists(path):
                    os.unlink(path)

    def test_detailed_payloads_carry_names(self, app, authed_client):
        _client, user_id, account_id = authed_client
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            assert account is not None
            domain = db.session.get(Domain, account.domain_id)
            assert domain is not None
            domain.matrix_host = "synapse"
            domain.matrix_port = 8008
            db.session.commit()
            settings = _seed_user(app, user_id)
            settings.push_detailed = True
            db.session.commit()
            path = _seed_chat_cache(user_id, account)
            try:
                with (
                    patch("app.shared.push.send_chat_message_push") as send_msg,
                    patch("app.shared.push.send_chat_call_push") as send_call,
                ):
                    self._tick(app)
                    assert send_msg.call_args.args[4] == "Peer"  # sender_name
                    assert send_msg.call_args.args[5] == "Peer"  # DM room display name
                    assert send_call.call_args.args[3] == "Peer"  # caller_name
            finally:
                if os.path.exists(path):
                    os.unlink(path)

    def test_skips_when_stream_active(self, app, authed_client):
        _client, user_id, account_id = authed_client
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            assert account is not None
            domain = db.session.get(Domain, account.domain_id)
            assert domain is not None
            domain.matrix_host = "synapse"
            db.session.commit()
            _seed_user(app, user_id)
            streams.register_stream(user_id)
            try:
                mock_client = self._tick(app)
                mock_client.return_value.sync.assert_not_called()
            finally:
                streams.unregister_stream(user_id)

    def test_skips_when_category_disabled(self, app, authed_client):
        _client, user_id, account_id = authed_client
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            assert account is not None
            domain = db.session.get(Domain, account.domain_id)
            assert domain is not None
            domain.matrix_host = "synapse"
            db.session.commit()
            settings = _seed_user(app, user_id)
            settings.notify_chat_enabled = False
            db.session.commit()
            mock_client = self._tick(app)
            mock_client.return_value.sync.assert_not_called()

    def test_skips_without_subscription(self, app, authed_client):
        _client, user_id, _account_id = authed_client
        with app.app_context():
            settings = CustomerSettings()
            settings.customer_id = user_id
            settings.notify_chat_enabled = True
            db.session.add(settings)
            db.session.commit()
            mock_client = self._tick(app)
            mock_client.return_value.sync.assert_not_called()

    def test_no_chat_credentials_skips_silently(self, app, authed_client):
        _client, user_id, account_id = authed_client
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            assert account is not None
            domain = db.session.get(Domain, account.domain_id)
            assert domain is not None
            domain.matrix_host = "synapse"
            db.session.commit()
            _seed_user(app, user_id)
            path = get_cache_path(account)
            conn = cache_db.open_cache(path, get_user_key(user_id))
            conn.close()
            try:
                with patch("app.shared.push.send_chat_message_push") as send_msg:
                    mock_client = self._tick(app)
                    send_msg.assert_not_called()
                    mock_client.return_value.sync.assert_not_called()
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(path)
