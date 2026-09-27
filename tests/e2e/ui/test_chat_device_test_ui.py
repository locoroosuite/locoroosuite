"""Chat device-test e2e (HLD U25.59): the test-call dialog opens from the
sidebar, fake media drives the preview, and the loopback test call reports a
working direct connection — one window, no peer session, no Matrix signaling.
"""

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import APP_URL, E2E_DEFAULT_PASSWORD

pytestmark = [skip_if_no_services]


@pytest.fixture(scope="function")
def fake_media_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=[
            # Auto-grant camera/mic prompts and serve a fake device so
            # getUserMedia resolves without real hardware.
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ],
    )
    context = browser.new_context(permissions=["camera", "microphone"])
    page = context.new_page()
    page.goto(f"{APP_URL}/app/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="email"]', "e2e-test@test.localhost")
    page.fill('input[name="password"]', E2E_DEFAULT_PASSWORD)
    page.click('button:has-text("Login")')
    page.wait_for_url("**/mail/**", timeout=10000)
    yield page
    context.close()
    browser.close()
    pw.stop()


def test_device_test_loopback_connects(fake_media_page):
    page = fake_media_page
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(f"{APP_URL}/app/chat/")
    page.wait_for_selector("#chat-test-call", state="visible", timeout=15000)
    page.click("#chat-test-call")

    page.locator("#chat-test-call-dialog").wait_for(state="visible", timeout=5000)
    # Fake device renders preview frames (readyState >= 2: HAVE_CURRENT_DATA).
    page.wait_for_function(
        "() => { const v = document.getElementById('chat-test-preview');"
        " return !!v && v.readyState >= 2; }",
        timeout=10000,
    )

    # Loopback: two local RTCPeerConnections — direct route must connect.
    page.click("#chat-test-loop")
    page.wait_for_function(
        "() => document.getElementById('chat-test-status-direct')"
        ".textContent.indexOf('working') !== -1",
        timeout=20000,
    )
    assert not errors, f"device test JS errors: {errors}"
