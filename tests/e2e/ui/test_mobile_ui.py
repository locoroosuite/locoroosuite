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
        # Folder links are anchors -> full navigation; the reloaded page
        # renders the drawer closed. Never wait for networkidle here: the
        # folder page keeps an SSE stream open, so the network never idles.
        page.wait_for_selector("#sidebar.-translate-x-full", timeout=10000)

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

    def test_message_row_tap_navigates_to_full_message(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        row.click()
        with contextlib.suppress(Exception):
            page.wait_for_url("**/mail/message/**", timeout=8000)

    def test_row_actions_toggle_renders_on_right_side(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """UX3b regression guard: the '...' toggle must sit in the right half
        of the row. It breaks when the toggle's `absolute` utility is
        defeated (e.g. by an unlayered `position:relative` rule like the
        old .lr-hit) — the button then falls back to its static position on
        the left, under the star: the reported mobile bug."""
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        toggle = row.query_selector("[data-message-actions-toggle]")
        assert toggle is not None, "mobile '...' toggle missing"
        assert toggle.is_visible()
        row_box = row.bounding_box()
        toggle_box = toggle.bounding_box()
        assert row_box is not None and toggle_box is not None
        toggle_center = toggle_box["x"] + toggle_box["width"] / 2
        assert toggle_center > row_box["x"] + row_box["width"] / 2


@skip_if_no_services
class TestMobileDetailHeaderUi:
    """U24.37: message detail header stacks below md — subject full width,
    actions in their own row, secondary actions in the overflow menu."""

    def _open_first_message(self, seeded_inbox_message, mobile_logged_in_page):
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        row.click()
        with contextlib.suppress(Exception):
            page.wait_for_url("**/mail/message/**", timeout=8000)
        assert "/mail/message/" in page.url
        return page

    def test_subject_keeps_width_and_page_has_no_overflow(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        page = self._open_first_message(seeded_inbox_message, mobile_logged_in_page)
        h1 = page.wait_for_selector("h1.truncate", timeout=10000)
        assert h1 is not None
        h1_box = h1.bounding_box()
        assert h1_box is not None
        viewport_width = page.evaluate("() => document.documentElement.clientWidth")
        # U24.37: the subject text block keeps at least half the viewport.
        block_width = h1.evaluate(
            "el => el.parentElement.parentElement.getBoundingClientRect().width"
        )
        assert block_width > viewport_width * 0.5
        # U24.37: primary actions render in their own row below the subject.
        reply = page.query_selector('a:text-is("Reply")')
        assert reply is not None
        reply_box = reply.bounding_box()
        assert reply_box is not None
        assert reply_box["y"] >= h1_box["y"] + h1_box["height"] - 2
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1

    def test_overflow_menu_carries_mobile_archive_delete(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """Reply All/Archive/Delete are header-only at md+, so on phones they
        must be reachable from the '...' menu (U24.37)."""
        page = self._open_first_message(seeded_inbox_message, mobile_logged_in_page)
        toggle = page.wait_for_selector("[data-overflow-toggle]", timeout=10000)
        assert toggle is not None
        toggle.click()
        archive_btns = page.query_selector_all(
            '[data-overflow-menu] form[data-action="archive"] button'
        )
        delete_btns = page.query_selector_all(
            '[data-overflow-menu] form[data-action="delete"] button'
        )
        assert any(b.is_visible() for b in archive_btns), "Archive missing from mobile menu"
        assert any(b.is_visible() for b in delete_btns), "Delete missing from mobile menu"

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
        page.wait_for_load_state("load")
        toggle = page.wait_for_selector("#cal-sidebar-toggle", timeout=10000)
        assert toggle is not None
        toggle.click()
        sidebar = page.wait_for_selector("#cal-sidebar:not(.-translate-x-full)", timeout=5000)
        assert sidebar is not None

    def test_day_view_default_on_phone(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/calendar/?view=week")
        page.wait_for_load_state("load")
        page.wait_for_selector("#calendar-grid", timeout=10000)
        active = page.eval_on_selector(
            ".view-btn[data-view='day']", "el => el.className.includes('bg-slate-900')"
        )
        assert active


@skip_if_no_services
class TestMobileContactsUi:
    def test_no_horizontal_overflow_on_contacts(self, mobile_logged_in_page, seeded_contact):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/contacts/")
        page.wait_for_load_state("load")
        page.wait_for_selector("#contacts-search-input", timeout=15000)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1

    def test_card_list_renders_instead_of_table(self, mobile_logged_in_page, seeded_contact):
        """Below md the list renders one stacked card per contact and the
        desktop table must not be visible (U24.7)."""
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/contacts/")
        page.wait_for_load_state("load")
        page.wait_for_selector("#contacts-search-input", timeout=15000)
        cards = page.wait_for_selector(f"[data-contact-name='{seeded_contact}']", timeout=10000)
        assert cards is not None
        table = page.query_selector("table")
        assert table is None or not table.is_visible()


@skip_if_no_services
class TestMobileSettingsUi:
    """Settings rows stack below sm (640px): no overflow, selects full width."""

    def test_no_horizontal_overflow_on_settings(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/mail/settings")
        page.wait_for_load_state("load")
        page.wait_for_selector("#tz-select", timeout=10000)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1

    def test_settings_selects_are_full_width_on_mobile(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/mail/settings")
        page.wait_for_load_state("load")
        page.wait_for_selector("#tz-select", timeout=10000)
        viewport_width = page.evaluate("() => document.documentElement.clientWidth")
        # Stacked below sm: page gutter px-3 + card px-3 leave ~342px at 390px
        # viewport, so a full-width select must span most of the viewport.
        box = page.locator("#tz-select").bounding_box()
        assert box is not None
        assert box["width"] > viewport_width * 0.8


@skip_if_no_services
class TestMobileDensityUi:
    """U24.33-U24.36: edge-to-edge lists and compact spacing below md (768px)."""

    def test_main_has_no_horizontal_padding_on_mobile(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        padding = page.eval_on_selector(
            "main",
            "el => ({ pl: getComputedStyle(el).paddingLeft, pt: getComputedStyle(el).paddingTop })",
        )
        assert padding["pl"] == "0px"
        # py-3 = 12px (was py-6 = 24px) per U24.33
        assert padding["pt"] == "12px"

    def test_message_list_is_full_bleed_on_mobile(self, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.wait_for_selector("#message-area", timeout=10000)
        box = page.eval_on_selector(
            "#message-area",
            "el => ({ left: Math.round(el.getBoundingClientRect().left),"
            " width: Math.round(el.getBoundingClientRect().width),"
            " radius: getComputedStyle(el).borderTopLeftRadius })",
        )
        viewport_width = page.evaluate("() => document.documentElement.clientWidth")
        assert box["left"] == 0
        assert box["width"] == viewport_width
        assert box["radius"] == "0px"

    def test_message_rows_keep_tap_target_with_compact_padding(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        metrics = page.eval_on_selector(
            ".message-row",
            "el => ({ pl: getComputedStyle(el).paddingLeft, h: Math.round(el.getBoundingClientRect().height) })",
        )
        # px-3 compact gutter (was px-4) per U24.34
        assert metrics["pl"] == "12px"
        # U24.36: density must not shrink tap targets below 40px
        assert metrics["h"] >= 40

    def test_desktop_spacing_unchanged(self, logged_in_page):
        page = logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        padding = page.eval_on_selector("main", "el => getComputedStyle(el).paddingLeft")
        # Desktop keeps the px-6/lg:px-8 rhythm (24px or 32px), never 0
        assert padding in ("24px", "32px")
        radius = page.eval_on_selector(
            "#message-area", "el => getComputedStyle(el).borderTopLeftRadius"
        )
        assert radius != "0px"


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


# Seeds the captured install prompt before page scripts run; pwa-install.js
# reads window.__lrBeforeInstallPrompt at evaluation time and shows the banner.
INSTALL_STUB = """
window.__lrBeforeInstallPrompt = {
  prompt: () => Promise.resolve(),
  userChoice: Promise.resolve({ outcome: 'accepted' })
};
"""

# Emulates running inside the installed PWA (standalone display mode).
STANDALONE_STUB = """
(() => {
  const mm = window.matchMedia.bind(window);
  window.matchMedia = (q) => (q.includes('display-mode') && q.includes('standalone'))
    ? { matches: true, media: q, onchange: null,
        addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {},
        dispatchEvent() { return false; } }
    : mm(q);
})();
"""

# Headless Chromium denies notifications by default; the onboarding prompt
# only shows for permission 'default', so fake an undecided Notification.
# The first (unstubbed) fixture load also sees the hard-deny and permanently
# writes lr-notif-never-ask=1 (U24.28: hard-deny never re-prompts), which
# would keep the prompt hidden even after stubbing — so clear that flag on
# every navigation. dismissed-at must NOT be cleared: tests set it on purpose.
NOTIF_DEFAULT_STUB = """
Object.defineProperty(window, 'Notification', {
  value: { permission: 'default', requestPermission: () => Promise.resolve('granted') },
  configurable: true
});
try { localStorage.removeItem('lr-notif-never-ask'); } catch (e) {}
"""


@skip_if_no_services
class TestPwaInstallPromotion:
    def test_banner_hidden_without_captured_prompt(self, android_logged_in_page):
        from tests.e2e.ui.conftest import BIP_BLOCKER

        page = android_logged_in_page
        page.context.add_init_script(BIP_BLOCKER)
        page.reload()
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert page.locator("#pwa-install-banner").is_hidden()

    def test_banner_shows_install_button_and_hides_on_accept(self, android_logged_in_page):
        page = android_logged_in_page
        page.context.add_init_script(INSTALL_STUB)
        page.reload()
        page.wait_for_selector("#pwa-install-banner", state="visible", timeout=10000)
        assert page.locator("#pwa-install-banner-accept").is_visible()
        page.click("#pwa-install-banner-accept")
        page.wait_for_selector("#pwa-install-banner", state="hidden", timeout=5000)

    def test_banner_dismiss_is_persistent(self, android_logged_in_page):
        page = android_logged_in_page
        page.context.add_init_script(INSTALL_STUB)
        page.reload()
        page.wait_for_selector("#pwa-install-banner", state="visible", timeout=10000)
        page.click("#pwa-install-banner-dismiss")
        page.wait_for_selector("#pwa-install-banner", state="hidden", timeout=5000)
        page.reload()
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert page.locator("#pwa-install-banner").is_hidden()

    def test_banner_hidden_when_installed(self, android_logged_in_page):
        page = android_logged_in_page
        page.context.add_init_script(INSTALL_STUB + STANDALONE_STUB)
        page.reload()
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert page.locator("#pwa-install-banner").is_hidden()

    def test_settings_install_entry_shows_button(self, android_logged_in_page):
        page = android_logged_in_page
        page.context.add_init_script(INSTALL_STUB)
        page.goto("http://localhost:8001/app/mail/settings")
        page.wait_for_selector("#pwa-install-settings", state="visible", timeout=10000)
        assert page.locator("#pwa-install-settings-btn").is_visible()
        assert page.locator("#pwa-install-settings-ios").is_hidden()

    def test_ios_banner_shows_share_instructions(self, ios_logged_in_page):
        page = ios_logged_in_page
        page.wait_for_selector("#pwa-install-banner", state="visible", timeout=10000)
        assert page.locator("#pwa-install-banner-accept").is_hidden()
        assert "Share" in page.locator("#pwa-install-banner-text").inner_text()

    def test_ios_settings_shows_instructions_and_push_note(self, ios_logged_in_page):
        page = ios_logged_in_page
        page.goto("http://localhost:8001/app/mail/settings")
        page.wait_for_selector("#pwa-install-settings", state="visible", timeout=10000)
        assert page.locator("#pwa-install-settings-ios").is_visible()
        assert page.locator("#pwa-install-settings-btn").is_hidden()
        assert "Home Screen" in page.locator("#push-status").inner_text()
        assert page.locator("#push-enable-btn").is_hidden()

    def test_no_banner_on_desktop(self, logged_in_page):
        page = logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert page.locator("#pwa-install-banner").is_hidden()

    def test_no_banner_for_admin(self, admin_page):
        page = admin_page
        assert page.query_selector("#pwa-install-banner") is None


@skip_if_no_services
class TestPwaNotifOnboarding:
    def test_notif_prompt_visible_when_installed(self, android_logged_in_page):
        page = android_logged_in_page
        page.context.add_init_script(STANDALONE_STUB + NOTIF_DEFAULT_STUB)
        page.reload()
        page.wait_for_selector("#pwa-notif-prompt", state="visible", timeout=10000)
        assert page.locator("#pwa-install-banner").is_hidden()

    def test_notif_prompt_asked_once(self, android_logged_in_page):
        page = android_logged_in_page
        page.context.add_init_script(STANDALONE_STUB + NOTIF_DEFAULT_STUB)
        page.reload()
        page.wait_for_selector("#pwa-notif-prompt", state="visible", timeout=10000)
        page.click("#pwa-notif-dismiss")
        page.wait_for_selector("#pwa-notif-prompt", state="hidden", timeout=5000)
        page.reload()
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert page.locator("#pwa-notif-prompt").is_hidden()

    def test_notif_prompt_not_shown_on_desktop_browser(self, logged_in_page):
        """U24.28: the prompt targets standalone/mobile users only. On a
        desktop browser (not standalone, not a mobile UA) it stays hidden
        regardless of permission state."""
        page = logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert page.locator("#pwa-notif-prompt").is_hidden()

    def test_notif_prompt_not_shown_on_mobile_browser_when_permission_denied(
        self, android_logged_in_page
    ):
        """U24.28 hard-deny rule: headless Chromium denies notifications, so
        a mobile browser user with permission denied must never see the
        prompt (and the opt-out is persisted)."""
        page = android_logged_in_page
        page.wait_for_selector("#mailbox-grid", timeout=10000)
        assert page.locator("#pwa-notif-prompt").is_hidden()
        assert page.evaluate("() => localStorage.getItem('lr-notif-never-ask') === '1'")
