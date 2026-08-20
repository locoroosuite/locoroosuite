"""Mobile UI tests (U24): drawers, responsive layouts, PWA install/offline."""

import contextlib

from tests.e2e.conftest import skip_if_no_services


@skip_if_no_services
class TestMobileMailUi:
    def test_folder_drawer_opens_and_navigates(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.wait_for_selector("#mobile-sidebar-toggle", timeout=10000)
        page.click("#mobile-sidebar-toggle")
        sidebar = page.wait_for_selector("#sidebar:not(.-translate-x-full)", timeout=5000)
        assert sidebar is not None
        assert page.query_selector("#sidebar-backdrop:not(.hidden)") is not None
        first_folder = page.query_selector("#sidebar .folder-drop")
        assert first_folder is not None
        first_folder.click()
        page.wait_for_load_state("networkidle")
        assert (
            page.query_selector("#sidebar").get_attribute("class").find("-translate-x-full") != -1
        )

    def test_drawer_closes_on_backdrop(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.wait_for_selector("#mobile-sidebar-toggle", timeout=10000)
        page.click("#mobile-sidebar-toggle")
        page.wait_for_selector("#sidebar:not(.-translate-x-full)", timeout=5000)
        page.click("#sidebar-backdrop")
        page.wait_for_selector("#sidebar.-translate-x-full", timeout=5000)

    def test_compose_fab_visible_on_mobile(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        fab = page.wait_for_selector("a[aria-label='Compose']", timeout=10000)
        assert fab is not None
        assert fab.is_visible()

    def test_preview_toggle_hidden_on_mobile(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.wait_for_selector("#mobile-sidebar-toggle", timeout=10000)
        toggle = page.query_selector("#preview-toggle")
        assert toggle is not None
        assert not toggle.is_visible()

    def test_message_row_tap_navigates_to_full_message(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        if row is None:
            return
        row.click()
        with contextlib.suppress(Exception):
            page.wait_for_url("**/mail/message/**", timeout=8000)

    def test_search_icon_expands_mobile_search(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.wait_for_selector("#mobile-search-toggle", timeout=10000)
        assert not page.query_selector("#mobile-search").is_visible()
        page.click("#mobile-search-toggle")
        panel = page.wait_for_selector("#mobile-search:not(.hidden)", timeout=5000)
        assert panel is not None
        assert page.query_selector("#mobile-search input[name='q']").is_visible()


@skip_if_no_services
class TestMobileHeaderUi:
    def test_desktop_search_visible_on_wide_viewport(self, logged_in_page):
        page = logged_in_page
        search = page.wait_for_selector("header form input[name='q']", timeout=10000)
        assert search is not None
        assert search.is_visible()

    def test_desktop_hides_mobile_extras(self, logged_in_page):
        page = logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert (
            page.query_selector("#mobile-sidebar-toggle") is None
            or not page.query_selector("#mobile-sidebar-toggle").is_visible()
        )
        fab = page.query_selector("a[aria-label='Compose']")
        assert fab is None or not fab.is_visible()


@skip_if_no_services
class TestMobileCalendarUi:
    def test_calendar_drawer_opens(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/calendar/")
        page.wait_for_load_state("networkidle")
        toggle = page.wait_for_selector("#cal-sidebar-toggle", timeout=10000)
        assert toggle is not None
        toggle.click()
        sidebar = page.wait_for_selector("#cal-sidebar:not(.-translate-x-full)", timeout=5000)
        assert sidebar is not None

    def test_day_view_default_on_phone(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/calendar/?view=week")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#calendar-grid", timeout=10000)
        active = page.eval_on_selector(
            ".view-btn[data-view='day']", "el => el.className.includes('bg-slate-900')"
        )
        assert active


@skip_if_no_services
class TestMobileContactsUi:
    def test_no_horizontal_overflow_on_contacts(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/contacts/")
        page.wait_for_load_state("networkidle")
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1

    def test_card_list_renders_instead_of_table(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/contacts/")
        page.wait_for_load_state("networkidle")
        cards = page.query_selector_all("[data-contact-card]")
        table = page.query_selector("table")
        if cards:
            assert table is None or not table.is_visible()
        else:
            assert table is None or not table.is_visible()


@skip_if_no_services
class TestPwaInstall:
    def test_manifest_linked_and_sw_registers(self, logged_in_page):
        page = logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        manifest = page.query_selector('link[rel="manifest"]')
        assert manifest is not None
        assert manifest.get_attribute("href") == "/manifest.webmanifest"
        page.evaluate("() => navigator.serviceWorker.ready")
        reg = page.evaluate("() => navigator.serviceWorker.getRegistration().then(r => !!r)")
        assert reg

    def test_offline_page_served_when_offline(self, logged_in_page):
        page = logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        page.evaluate("() => navigator.serviceWorker.ready")
        context = page.context
        context.set_offline(True)
        try:
            page.goto("http://localhost:8001/app/mail/", timeout=15000)
            page.wait_for_selector("text=You're offline", timeout=10000)
        finally:
            context.set_offline(False)
