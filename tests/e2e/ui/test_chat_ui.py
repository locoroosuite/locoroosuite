"""Chat UI e2e: delivery ticks render on own DM messages (HLD U25.18).

Creates a DM + sends a message through the module API using the logged-in
browser session, then verifies the timeline renders the tick cluster and
the page boots without JS errors.
"""

import json

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import APP_URL

pytestmark = [skip_if_no_services]

PEER_EMAIL = "e2e-test2@test.localhost"


def test_own_dm_message_shows_sent_tick(logged_in_page):
    page = logged_in_page
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    # Create the DM and send a message through the module API (same session).
    resp = page.request.post(
        f"{APP_URL}/app/chat/api/dm",
        data=json.dumps({"email": PEER_EMAIL}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 201, f"DM creation failed: {resp.status} {resp.text()}"
    room_id = resp.json()["room"]["room_id"]
    resp = page.request.post(
        f"{APP_URL}/app/chat/api/rooms/{room_id}/send",
        data=json.dumps({"body": "tick rendering probe"}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 201, f"send failed: {resp.status} {resp.text()}"

    # Open the chat page and the DM room.
    page.goto(f"{APP_URL}/app/chat/")
    page.wait_for_selector("#chat-dms li", timeout=15000)
    page.click("#chat-dms li:first-child")
    page.wait_for_selector("#chat-messages div[data-event-id]", timeout=15000)

    # The own message row renders the tick cluster with a translated title.
    tick = page.wait_for_selector('#chat-messages span[title="Message sent"]', timeout=10000)
    assert tick is not None
    assert not errors, f"chat page JS errors: {errors}"
