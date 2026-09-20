import pytest

from tests.e2e.services import E2E_DEFAULT_PASSWORD


@pytest.fixture(scope="function")
def page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
        return
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context()
    p = context.new_page()
    yield p
    context.close()
    browser.close()
    pw.stop()


@pytest.fixture(scope="function")
def logged_in_page(page):
    page.goto("http://localhost:8001/app/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="email"]', "e2e-test@test.localhost")
    page.fill('input[name="password"]', E2E_DEFAULT_PASSWORD)
    page.click('button:has-text("Login")')
    page.wait_for_url("**/mail/**", timeout=10000)
    yield page


@pytest.fixture(scope="function")
def mobile_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
        return
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
    )
    p = context.new_page()
    yield p
    context.close()
    browser.close()
    pw.stop()


@pytest.fixture(scope="function")
def mobile_logged_in_page(mobile_page):
    mobile_page.goto("http://localhost:8001/app/login")
    mobile_page.wait_for_load_state("networkidle")
    mobile_page.fill('input[name="email"]', "e2e-test@test.localhost")
    mobile_page.fill('input[name="password"]', E2E_DEFAULT_PASSWORD)
    mobile_page.click('button:has-text("Login")')
    mobile_page.wait_for_url("**/mail/**", timeout=30000)
    yield mobile_page


# Blocks the real beforeinstallprompt event so tests control the captured
# prompt state deterministically (headless Chromium may or may not fire it).
BIP_BLOCKER = """
window.addEventListener('beforeinstallprompt', function (e) {
  if (e.stopImmediatePropagation) e.stopImmediatePropagation();
}, true);
"""


@pytest.fixture(scope="function")
def android_logged_in_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
        return
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 412, "height": 915},
        is_mobile=True,
        has_touch=True,
        user_agent=(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
    )
    p = context.new_page()
    p.goto("http://localhost:8001/app/login")
    p.wait_for_load_state("networkidle")
    p.fill('input[name="email"]', "e2e-test@test.localhost")
    p.fill('input[name="password"]', E2E_DEFAULT_PASSWORD)
    p.click('button:has-text("Login")')
    p.wait_for_url("**/mail/**", timeout=30000)
    yield p
    context.close()
    browser.close()
    pw.stop()


@pytest.fixture(scope="function")
def ios_logged_in_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
        return
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1"
        ),
    )
    # iOS never fires beforeinstallprompt; suppress any headless-Chromium firing.
    context.add_init_script(BIP_BLOCKER)
    p = context.new_page()
    p.goto("http://localhost:8001/app/login")
    p.wait_for_load_state("networkidle")
    p.fill('input[name="email"]', "e2e-test@test.localhost")
    p.fill('input[name="password"]', E2E_DEFAULT_PASSWORD)
    p.click('button:has-text("Login")')
    p.wait_for_url("**/mail/**", timeout=30000)
    yield p
    context.close()
    browser.close()
    pw.stop()


@pytest.fixture(scope="function")
def seeded_inbox_message():
    """Seed one message into e2e-test@test.localhost's INBOX via the app's
    send endpoint, then wait until the app's own folder view lists it.

    Pages under test keep an SSE stream open, so tests must never rely on
    networkidle; this fixture guarantees the browser renders a .message-row
    on first load without depending on background-sync timing.
    """
    import uuid

    from tests.e2e.services import (
        APP_URL,
        get_account_id,
        imap_folder_has_message,
        login_session,
        wait_for,
    )

    subject = f"E2E UI {uuid.uuid4().hex[:8]}"
    session = login_session("e2e-test@test.localhost")
    account_id = get_account_id(APP_URL, session)
    r = session.post(
        f"{APP_URL}/app/mail/send",
        data={
            "account_id": account_id,
            "to": "e2e-test@test.localhost",
            "subject": subject,
            "body_html": f"<p>{subject}</p>",
        },
        allow_redirects=True,
    )
    assert r.status_code == 200, f"send failed: {r.status_code}"
    assert imap_folder_has_message(
        "e2e-test@test.localhost",
        E2E_DEFAULT_PASSWORD,
        "INBOX",
        subject_contains=subject,
        timeout=30,
    )

    def folder_lists_subject() -> bool:
        resp = session.get(f"{APP_URL}/app/mail/folder/{account_id}/INBOX")
        return resp.status_code == 200 and subject in resp.text

    wait_for(folder_lists_subject, timeout=30)
    return {"subject": subject, "account_id": account_id}


@pytest.fixture(scope="function")
def admin_page(page):
    page.goto("http://localhost:8001/admin/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="email"]', "admin@dev.test")
    page.fill('input[name="password"]', E2E_DEFAULT_PASSWORD)
    page.click('button:has-text("Login")')
    page.wait_for_url("**/admin/**", timeout=10000)
    yield page
