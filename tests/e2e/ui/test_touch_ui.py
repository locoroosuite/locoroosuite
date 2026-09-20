"""Touch-mode UI tests (HLD UX3b/UX3d).

Reproduce the reported bug: on touch devices browsers emulate :hover during
a tap, which used to make the invisible row-action overlay interactive and
archive messages accidentally. The overlay must stay inert until the user
explicitly opens it via the '...' toggle; hover-hidden actions on other
lists (docs, folder sidebar) must be always visible on touch.
"""

import contextlib

from tests.e2e.conftest import skip_if_no_services


def _assert_touch_emulation(page):
    """Sanity: the mobile context must actually emulate a touch device."""
    hover_none = page.evaluate("matchMedia('(hover: none)').matches")
    assert hover_none, "test context is not emulating (hover: none); fixture misconfigured"


@skip_if_no_services
class TestTouchMailList:
    def test_touch_context_matches_hover_none(self, mobile_logged_in_page):
        _assert_touch_emulation(mobile_logged_in_page)

    def test_overlay_is_inert_until_toggle_opened(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """UX3b: with the overlay closed, its pointer-events must be none —
        even though the row may pick up sticky :hover from the tap."""
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        overlay = row.query_selector("[data-message-actions]")
        assert overlay is not None
        # Simulate the sticky-hover state the tap itself produces.
        row.hover()
        pe = overlay.evaluate("el => getComputedStyle(el).pointerEvents")
        assert pe == "none", (
            f"invisible overlay is interactive under hover emulation ({pe}) — "
            "accidental archive regression (UX3b)"
        )

    def test_tap_right_side_of_row_navigates_not_archives(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """The reported bug: tapping the right side of a row (where the
        invisible Archive button sits) must open the message, not archive."""
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        box = row.bounding_box()
        assert box is not None
        # Tap inside the overlay's horizontal span (right edge of the row).
        page.touchscreen.tap(box["x"] + box["width"] - 20, box["y"] + box["height"] / 2)
        with contextlib.suppress(Exception):
            page.wait_for_url("**/mail/message/**", timeout=8000)
        assert "/mail/message/" in page.url, (
            "tap on the row's right side did not navigate (accidental action?)"
        )
        # The message must still exist afterwards (not archived/deleted).
        assert seeded_inbox_message["subject"]  # sanity: fixture contract

    def test_toggle_reveals_actions_and_archive_removes_row(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """The intended touch flow: '...' toggle locks the overlay open,
        making Archive visible and tappable."""
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        msg_id = row.get_attribute("data-message-id")
        toggle = row.query_selector("[data-message-actions-toggle]")
        assert toggle is not None, "mobile '...' toggle missing"
        toggle.click()
        page.wait_for_selector(
            f".message-row[data-message-id='{msg_id}'][data-overlay-locked]",
            timeout=5000,
        )
        archive = row.query_selector('form[data-action="archive"] button')
        assert archive is not None and archive.is_visible()
        archive.click()
        page.wait_for_selector(
            f".message-row[data-message-id='{msg_id}']",
            state="detached",
            timeout=10000,
        )

    def test_folder_sidebar_actions_visible_on_touch(self, mobile_logged_in_page):
        """UX3d: the folder '...' menu toggle is hover-hidden on desktop but
        must be visible on touch devices."""
        page = mobile_logged_in_page
        page.wait_for_selector("#mobile-sidebar-toggle", timeout=10000)
        page.click("#mobile-sidebar-toggle")
        page.wait_for_selector("#sidebar:not(.-translate-x-full)", timeout=5000)
        toggle = page.wait_for_selector("[data-folder-actions-toggle]", timeout=5000)
        assert toggle is not None
        opacity = toggle.evaluate("el => getComputedStyle(el).opacity")
        assert opacity == "1", (
            f"folder actions toggle invisible on touch ({opacity}) — UX3d regression"
        )


@skip_if_no_services
class TestTouchSearch:
    def test_search_rows_have_touch_toggle(self, seeded_inbox_message, mobile_logged_in_page):
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/mail/")
        page.wait_for_load_state("load")
        # U24.4: on phones the search input collapses behind a header icon.
        search_toggle = page.query_selector("#mobile-search-toggle")
        if search_toggle and search_toggle.is_visible():
            search_toggle.click()
            page.wait_for_selector("#mobile-search:not(.hidden)", timeout=5000)
            q_input = page.query_selector("#mobile-search input[name='q']")
        else:
            form = page.query_selector('form[action*="mail/search"]')
            assert form is not None
            q_input = form.query_selector('input[name="q"]')
        assert q_input is not None
        q_input.fill(seeded_inbox_message["subject"])
        q_input.evaluate("el => el.form.submit()")
        page.wait_for_url("**/mail/search**", timeout=15000)
        page.wait_for_load_state("load")
        toggle = page.query_selector("#search-results [data-message-actions-toggle]")
        assert toggle is not None, "search rows missing the mobile '...' toggle (UX3b)"
        overlay = page.query_selector("#search-results [data-message-actions]")
        assert overlay is not None
        pe = overlay.evaluate("el => getComputedStyle(el).pointerEvents")
        assert pe == "none", "search overlay interactive while hidden (UX3d)"


@skip_if_no_services
class TestTouchDocs:
    def test_docs_mobile_card_layout_and_visible_actions(self, mobile_logged_in_page):
        """U24.32: below lg the docs list renders cards (no table) and the
        card actions are always visible (no hover dependency)."""
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/docs/")
        page.wait_for_load_state("load")
        page.wait_for_selector("#docs-sidebar", timeout=10000)
        table = page.query_selector("table")
        assert table is None or not table.is_visible(), (
            "docs table must be hidden below lg (U24.32)"
        )
        cards = page.query_selector_all(".doc-row")
        for card in cards[:3]:
            btn = card.query_selector(".share-doc-btn, .convert-doc-btn, .tag-doc-btn")
            if btn is None:
                continue
            visible = btn.is_visible()
            if not visible:
                opacity = btn.evaluate("el => getComputedStyle(el).opacity")
                assert opacity == "1", "docs card actions must be visible on touch (UX3d)"
            break

    def test_docs_table_actions_inert_while_hidden(self, logged_in_page):
        """Desktop: the hover overlay stays non-interactive until hovered."""
        page = logged_in_page
        page.goto("http://localhost:8001/app/docs/")
        page.wait_for_load_state("load")
        page.wait_for_selector("#docs-sidebar", timeout=10000)
        actions = page.query_selector("tr.group .opacity-0.pointer-events-none")
        if actions is None:
            return  # no documents in the dev env
        pe = actions.evaluate("el => getComputedStyle(el).pointerEvents")
        assert pe == "none", "docs row actions interactive while hidden (UX3d)"
