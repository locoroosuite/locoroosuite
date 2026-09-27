"""Chat delivery ticks e2e (HLD U25.18): send → delivered → read.

Service-level: drives two real suite sessions through the chat module API
against the dev Synapse + MAS, verifying the per-message status
transitions end to end (DM creation, lazy MAS provisioning, app-internal
delivery recording, and the public m.read receipt path).
"""

import uuid

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import APP_URL, wait_for

pytestmark = [skip_if_no_services]

PEER_EMAIL = "e2e-test2@test.localhost"


def _sync_now(session):
    resp = session.post(f"{APP_URL}/app/chat/api/sync-now", timeout=60)
    resp.raise_for_status()
    return resp.json()


def _messages(session, room_id):
    resp = session.get(f"{APP_URL}/app/chat/api/rooms/{room_id}/messages?limit=50", timeout=60)
    resp.raise_for_status()
    return resp.json()


def _status_of(session, room_id, event_id):
    data = _messages(session, room_id)
    for message in data["messages"]:
        if message["event_id"] == event_id:
            return message.get("status")
    return None


def test_dm_delivery_and_read_ticks(user_session, user_b_session):
    sender, recipient = user_session, user_b_session
    body = f"ticks probe {uuid.uuid4().hex[:8]}"

    # Sender starts a DM (provisions the peer's chat identity via MAS).
    resp = sender.post(f"{APP_URL}/app/chat/api/dm", json={"email": PEER_EMAIL}, timeout=60)
    assert resp.status_code == 201, resp.text
    room_id = resp.json()["room"]["room_id"]

    # Recipient syncs, sees the invite, and joins the room.
    _sync_now(recipient)
    resp = recipient.post(f"{APP_URL}/app/chat/api/rooms/{room_id}/join", timeout=60)
    assert resp.status_code == 200, resp.text

    # Sender sends a message.
    resp = sender.post(
        f"{APP_URL}/app/chat/api/rooms/{room_id}/send", json={"body": body}, timeout=60
    )
    assert resp.status_code == 201, resp.text
    event_id = resp.json()["event_id"]

    # Before the recipient syncs: delivered to the server only ("sent").
    _sync_now(sender)
    assert _status_of(sender, room_id, event_id) == "sent"

    # Recipient's sync ingests the message → app-internal delivery row.
    _sync_now(recipient)
    assert wait_for(lambda: _status_of(sender, room_id, event_id) == "delivered", timeout=15), (
        "message should be delivered after the recipient's sync"
    )

    # Recipient opens the room and marks it read → public m.read receipt.
    data = _messages(recipient, room_id)
    last = data["messages"][-1]
    resp = recipient.post(
        f"{APP_URL}/app/chat/api/rooms/{room_id}/read",
        json={"event_id": last["event_id"]},
        timeout=60,
    )
    assert resp.status_code == 200, resp.text

    # Sender's sync pulls the receipt into their cache → "read".
    assert wait_for(
        lambda: _sync_now(sender) and _status_of(sender, room_id, event_id) == "read",
        timeout=15,
    ), "message should be read after the recipient's m.read receipt"

    # Receipts are part of the API contract (U25.18).
    receipts = _messages(sender, room_id)["receipts"]
    assert any(r["receipt_type"] == "m.read" for r in receipts)
