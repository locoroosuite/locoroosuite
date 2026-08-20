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
    mobile_page.wait_for_url("**/mail/**", timeout=10000)
    yield mobile_page


@pytest.fixture(scope="function")
def admin_page(page):
    page.goto("http://localhost:8001/admin/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="email"]', "admin@dev.test")
    page.fill('input[name="password"]', E2E_DEFAULT_PASSWORD)
    page.click('button:has-text("Login")')
    page.wait_for_url("**/admin/**", timeout=10000)
    yield page
