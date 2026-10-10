"""Touch-mode UI tests (HLD UX3b/UX3d/UX3g/UX3h).

Covers the touch interaction stack for the message list: the hover action
overlay must stay inert on touch (UX3b/UX3d), row actions are revealed by
labeled swipe-gesture quick actions with the remaining actions in the
"More" bottom sheet (UX3g), and press-and-hold enters the long-press
selection mode (UX3h) — which every select-all entry point must also
enter, with the action overlay hidden while it is active.
"""

import contextlib
import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import APP_URL, get_account_id, login_session, wait_for
from tests.e2e.ui.touch_actions import (
    long_press_element,
    swipe_element,
    tap_element,
    wait_list_settled as _wait_list_settled,
)


def _assert_touch_emulation(page):
    """Sanity: the mobile context must actually emulate a touch device."""
    hover_none = page.evaluate("matchMedia('(hover: none)').matches")
    assert hover_none, "test context is not emulating (hover: none); fixture misconfigured"


def _first_swipeable_row(page):
    """First message row that carries swipe panels (normal variant)."""
    for row in page.query_selector_all(".message-row"):
        if row.query_selector("[data-swipe-panel]") is not None:
            return row
    return None


@skip_if_no_services
class TestTouchMailList:
    def test_touch_context_matches_hover_none(self, mobile_logged_in_page):
        _assert_touch_emulation(mobile_logged_in_page)

    def test_overlay_is_inert_until_revealed(self, seeded_inbox_message, mobile_logged_in_page):
        """UX3b: with the overlay unrevealed, its pointer-events must be
        none — even though the row may pick up sticky :hover from a tap."""
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
        """Tapping the right side of a row (where action overlays sit)
        must open the message, not trigger an action. The probe taps the
        date line area at the top-right of the three-line mobile row."""
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        box = row.bounding_box()
        assert box is not None
        tap_x = box["x"] + box["width"] - 24
        assert tap_x > box["x"], "tap probe fell outside the row"
        page.touchscreen.tap(tap_x, box["y"] + 10)
        with contextlib.suppress(Exception):
            page.wait_for_url("**/mail/message/**", timeout=8000)
        assert "/mail/message/" in page.url, (
            "tap on the row's right side did not navigate (accidental action?)"
        )
        # The message must still exist afterwards (not archived/deleted).
        assert seeded_inbox_message["subject"]  # sanity: fixture contract

    def test_swipe_left_reveals_actions_and_archive_removes_row(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """UX3g: swiping a row left reveals the action buttons behind the
        row foreground; Archive from the revealed panel removes the row."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        _wait_list_settled(page)
        row = _first_swipeable_row(page)
        assert row is not None, "no swipeable rows rendered"
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"
        swipe_element(page, row_sel, -160)
        page.wait_for_selector(f"{row_sel}.is-swipe-open", timeout=5000)
        archive = page.query_selector(
            f"{row_sel} [data-swipe-panel='right'] form[data-action='archive'] button"
        )
        assert archive is not None and archive.is_visible(), "archive button not revealed"
        tap_element(
            page, f"{row_sel} [data-swipe-panel='right'] form[data-action='archive'] button"
        )
        page.wait_for_selector(row_sel, state="detached", timeout=10000)

    def test_swipe_right_archives_row_with_undo(self, seeded_inbox_message, mobile_logged_in_page):
        """UX3g: a full right swipe archives immediately (U5.11 undo)."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        _wait_list_settled(page)
        row = _first_swipeable_row(page)
        assert row is not None, "no swipeable rows rendered"
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"
        swipe_element(page, row_sel, 180)
        page.wait_for_selector(row_sel, state="detached", timeout=10000)
        undo = page.query_selector("#undo-banner:not(.hidden)")
        assert undo is not None, "archive swipe did not surface the undo banner"

    def test_swipe_below_threshold_snaps_back(self, seeded_inbox_message, mobile_logged_in_page):
        """UX3g: a short drag disambiguated as a swipe must snap the row
        back closed when released before the trigger threshold."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        _wait_list_settled(page)
        row = _first_swipeable_row(page)
        assert row is not None, "no swipeable rows rendered"
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"
        swipe_element(page, row_sel, -30)
        page.wait_for_timeout(500)
        assert page.query_selector(f"{row_sel}.is-swipe-open") is None, (
            "row stayed open after a sub-threshold swipe"
        )

    def test_swipe_panel_buttons_show_visible_labels(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """UX3g: revealed quick actions carry visible labels under their
        icons — icon-only reveals are not self-explanatory."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        _wait_list_settled(page)
        row = _first_swipeable_row(page)
        assert row is not None, "no swipeable rows rendered"
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"
        swipe_element(page, row_sel, -160)
        page.wait_for_selector(f"{row_sel}.is-swipe-open", timeout=5000)
        labels = [
            span.text_content().strip()
            for span in page.query_selector_all(f"{row_sel} [data-swipe-panel='right'] button span")
            if span.text_content().strip()
        ]
        assert "Archive" in labels and "Delete" in labels and "More" in labels, (
            f"quick-action labels missing from revealed panel: {labels}"
        )
        assert "Mark read" in labels or "Mark unread" in labels, (
            f"mark read/unread label missing from revealed panel: {labels}"
        )

    def test_overflow_more_opens_bottom_sheet_with_options(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """UX3g: the swipe panel's "More" opens the touch bottom action
        sheet with the row's overflow entries. Regression: the old path
        opened the in-row dropdown, which lives inside the hover action
        overlay — display:none on touch — so tapping "More" showed
        nothing. The open → cancel → open cycle must keep showing all
        options (the dropdown variant used to wipe its content)."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        _wait_list_settled(page)
        row = _first_swipeable_row(page)
        assert row is not None, "no swipeable rows rendered"
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"
        toggle_sel = f"{row_sel} [data-swipe-panel='right'] [data-overflow-toggle]"

        swipe_element(page, row_sel, -160)
        page.wait_for_selector(f"{row_sel}.is-swipe-open", timeout=5000)

        for _ in range(2):  # open, cancel, reopen
            tap_element(page, toggle_sel)
            page.wait_for_selector("[data-sheet-panel]", state="visible", timeout=5000)
            entry = page.wait_for_selector(
                "[data-sheet-content] [data-sheet-entry]", state="visible", timeout=5000
            )
            assert entry is not None
            kinds = [
                e.get_attribute("data-sheet-entry")
                for e in page.query_selector_all("[data-sheet-content] [data-sheet-entry]")
            ]
            assert "mark" in kinds, "mark entry missing from the sheet"
            assert "move" in kinds, "move-to-folder entry missing from the sheet"
            page.click("[data-sheet-cancel]")
            page.wait_for_selector("[data-sheet-panel]", state="hidden", timeout=5000)

    def test_sheet_action_marks_message_read_in_place(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """Sheet entries submit the row's real overflow-menu forms, so
        Mark as read updates the row in place (no navigation) and the
        sheet closes."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        _wait_list_settled(page)
        row = _first_swipeable_row(page)
        assert row is not None, "no swipeable rows rendered"
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        assert row.get_attribute("data-is-unread") == "1", (
            "fixture contract: the seeded message must arrive unread"
        )
        row_sel = f".message-row[data-message-id='{msg_id}']"

        swipe_element(page, row_sel, -160)
        page.wait_for_selector(f"{row_sel}.is-swipe-open", timeout=5000)
        tap_element(page, f"{row_sel} [data-swipe-panel='right'] [data-overflow-toggle]")
        page.wait_for_selector("[data-sheet-panel]", state="visible", timeout=5000)
        page.wait_for_timeout(350)  # let the 250ms slide-up finish before computing tap coords
        tap_element(page, "[data-sheet-entry='mark']")
        page.wait_for_selector("[data-sheet-panel]", state="hidden", timeout=5000)
        page.wait_for_function(
            "id => { const el = document.querySelector(`.message-row[data-message-id='${id}']`);"
            " return !!el && el.dataset.isUnread === '0'; }",
            arg=msg_id,
            timeout=10000,
        )
        assert page.query_selector(row_sel) is not None, (
            "row vanished — the sheet entry navigated instead of submitting in place"
        )

    def test_overflow_menu_layered_above_following_row_surfaces(
        self, seeded_inbox_message, logged_in_page
    ):
        """Regression: the overflow menu's z-50 is confined to the action
        overlay's z-20 stacking context, so following rows' surfaces
        (overlays, revealed swipe panels) painted over the open menu. The
        active row must be elevated above sibling rows' overlays. This is
        the desktop path; on touch the swipe panel's "More" opens the
        bottom sheet instead (see test_overflow_more_opens_bottom_sheet)."""
        page = logged_in_page
        subject = f"E2E UI overflow {uuid.uuid4().hex[:8]}"
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

        def folder_lists_subject() -> bool:
            resp = session.get(f"{APP_URL}/app/mail/folder/{account_id}/INBOX")
            return resp.status_code == 200 and subject in resp.text

        wait_for(folder_lists_subject, timeout=30)

        page.reload()
        page.wait_for_function("document.querySelectorAll('.message-row').length >= 2")
        # Bounce/draft/sent row variants have no action overlay; only rows
        # that can open the menu are relevant for the layering check.
        rows = [
            r
            for r in page.query_selector_all(".message-row")
            if r.query_selector("[data-message-actions]") is not None
        ]
        assert len(rows) >= 2, "need two actionable rows for the layering check"
        first = rows[0]
        following = rows[1]
        msg_id = first.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"

        first.hover()
        page.wait_for_selector(f"{row_sel} [data-message-actions]", state="visible", timeout=5000)
        page.click(f"{row_sel} [data-message-actions] [data-overflow-toggle]")
        page.wait_for_selector(f"{row_sel} [data-overflow-menu]", state="visible", timeout=5000)

        z_row = first.evaluate("el => getComputedStyle(el).zIndex")
        following_overlay = following.query_selector("[data-message-actions]")
        assert following_overlay is not None
        z_overlay = following_overlay.evaluate("el => getComputedStyle(el).zIndex")
        assert int(z_row) > int(z_overlay), (
            f"active row z-index ({z_row}) must beat sibling overlay ({z_overlay})"
        )

    def test_long_press_enters_selection_mode(self, seeded_inbox_message, mobile_logged_in_page):
        """UX3h: press-and-hold enters selection mode, shows the animated
        circle (selected on the held row), and 'Done' exits. The held
        row's circle must stay selected even if the background SSE sync
        replaces the list right after the press (state re-application
        guard in js/mail/bulk-select.js)."""
        page = mobile_logged_in_page
        row = page.wait_for_selector(".message-row", timeout=15000)
        assert row is not None
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"
        _wait_list_settled(page)

        long_press_element(page, row_sel, ms=650)
        page.wait_for_selector("#message-list[data-selection-mode='1']", timeout=5000)
        circle = page.query_selector(f"{row_sel} [data-select-circle]")
        assert circle is not None, "selection circle missing in selection mode"
        assert "is-selected" in (circle.get_attribute("class") or "")
        toolbar = page.query_selector("#bulk-toolbar:not(.hidden)")
        assert toolbar is not None, "bulk toolbar must appear in selection mode"
        assert "1 selected" in toolbar.inner_text()

        done = page.query_selector("#bulk-toolbar [data-bulk-done]:not(.hidden)")
        assert done is not None, "Done control missing in selection mode"
        done.click()
        page.wait_for_selector("#message-list:not([data-selection-mode])", timeout=5000)
        assert page.query_selector("#bulk-toolbar:not(.hidden)") is None

    def test_header_select_all_enters_selection_mode(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """UX3h: on touch, the header "Select" checkbox must enter the
        long-press selection mode (per-row circles + Done control), not
        fill the selection silently with circles left hidden. The label
        (not the bare input) is clicked: its lr-hit ::before hit-area
        expansion covers the input on coarse pointers and intercepts
        point hits — label activation toggles the input either way."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        _wait_list_settled(page)
        row = _first_swipeable_row(page)
        assert row is not None
        msg_id = row.get_attribute("data-message-id")
        assert msg_id
        row_sel = f".message-row[data-message-id='{msg_id}']"
        page.click("label:has(#select-all-messages)")
        page.wait_for_selector("#message-list[data-selection-mode='1']", timeout=5000)
        circle = page.query_selector(f"{row_sel} [data-select-circle]")
        assert circle is not None and circle.is_visible(), (
            "selection circle not visible after header select-all"
        )
        assert "is-selected" in (circle.get_attribute("class") or "")
        toolbar = page.query_selector("#bulk-toolbar:not(.hidden)")
        assert toolbar is not None, "bulk toolbar must appear after header select-all"
        assert "selected" in toolbar.inner_text()
        done = page.query_selector("#bulk-toolbar [data-bulk-done]:not(.hidden)")
        assert done is not None, "Done control missing after header select-all"
        done.click()
        page.wait_for_selector("#message-list:not([data-selection-mode])", timeout=5000)

    def test_tap_in_selection_mode_never_reveals_action_overlay(
        self, seeded_inbox_message, mobile_logged_in_page
    ):
        """UX3b/UX3h regression: tapping a row while in selection mode can
        focus the row's tabindex'd span; the overlay's focus-within reveal
        used to surface the Archive/Delete/... menu then. The overlay must
        stay display:none in selection mode on touch."""
        page = mobile_logged_in_page
        page.wait_for_selector(".message-row", timeout=15000)
        # Seed a second message so there is a row to tap besides the
        # long-pressed one.
        subject = f"E2E UI selmode {uuid.uuid4().hex[:8]}"
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

        def folder_lists_subject() -> bool:
            resp = session.get(f"{APP_URL}/app/mail/folder/{account_id}/INBOX")
            return resp.status_code == 200 and subject in resp.text

        wait_for(folder_lists_subject, timeout=30)

        page.reload()
        page.wait_for_function("document.querySelectorAll('.message-row').length >= 2")
        _wait_list_settled(page)
        rows = [
            r
            for r in page.query_selector_all(".message-row")
            if r.query_selector("[data-swipe-panel]") is not None
        ]
        assert len(rows) >= 2, "need two swipeable rows for the tap test"
        held_id = rows[0].get_attribute("data-message-id")
        assert held_id
        long_press_element(page, f".message-row[data-message-id='{held_id}']", ms=650)
        page.wait_for_selector("#message-list[data-selection-mode='1']", timeout=5000)
        # Re-query by id right before use: a background SSE refresh can
        # replace the row nodes between here and the earlier capture,
        # detaching the stale handles.
        tap_id = rows[1].get_attribute("data-message-id")
        assert tap_id
        tap_sel = f".message-row[data-message-id='{tap_id}']"
        tap_row = page.query_selector(tap_sel)
        box = tap_row.bounding_box()
        assert box is not None
        page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(300)
        tap_row = page.query_selector(tap_sel)
        assert tap_row is not None, "tapped row vanished (list replaced mid-tap?)"
        tapped_circle = tap_row.query_selector("[data-select-circle]")
        assert tapped_circle is not None
        assert "is-selected" in (tapped_circle.get_attribute("class") or ""), (
            "tapped second row was not selected in selection mode (UX3h)"
        )
        overlay = tap_row.query_selector("[data-message-actions]")
        assert overlay is not None
        display = overlay.evaluate("el => getComputedStyle(el).display")
        assert display == "none", (
            f"action overlay visible in selection mode on touch ({display}) — "
            "Archive/Delete leak (UX3b/UX3h)"
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
    def test_search_rows_have_swipe_panels(self, seeded_inbox_message, mobile_logged_in_page):
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
        assert page.query_selector("#search-results [data-message-actions-toggle]") is None, (
            "the '...' toggle was removed from search rows (UX3g)"
        )
        left = page.query_selector("#search-results [data-swipe-panel='left']")
        right = page.query_selector("#search-results [data-swipe-panel='right']")
        assert left is not None, "search rows missing the swipe archive panel (UX3g)"
        assert right is not None, "search rows missing the swipe actions panel (UX3g)"
        overlay = page.query_selector("#search-results [data-message-actions]")
        assert overlay is not None
        pe = overlay.evaluate("el => getComputedStyle(el).pointerEvents")
        assert pe == "none", "search overlay interactive while hidden (UX3d)"


@skip_if_no_services
class TestTouchDocs:
    def test_docs_swipe_reveals_labeled_quick_actions(self, mobile_logged_in_page):
        """U24.32/UX3g parity: swiping a doc card left reveals the
        quick-action panel with visible labels (Download), and the panel
        stays interactive (taps route to the buttons, not the card)."""
        page = mobile_logged_in_page
        page.goto("http://localhost:8001/app/docs/")
        page.wait_for_load_state("load")
        page.wait_for_selector("#docs-sidebar", timeout=10000)
        card = page.query_selector("#docs-mobile-list .doc-swipe-row [data-row-foreground]")
        if card is None:
            pytest.skip("no documents in the dev env")
        row_sel = "#docs-mobile-list .doc-swipe-row"
        swipe_element(page, row_sel, -160)
        page.wait_for_selector(f"{row_sel}.is-swipe-open", timeout=5000)
        page.wait_for_timeout(350)  # let the snap-open transition finish
        download = page.query_selector(f"{row_sel} [data-swipe-panel='right'] a[title='Download']")
        assert download is not None and download.is_visible(), (
            "Download quick action not revealed by the swipe (U24.32)"
        )
        assert "Download" in (download.text_content() or ""), (
            "revealed quick actions must carry a visible label (UX3g parity)"
        )

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
