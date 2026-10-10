"""Static-asset guards for mail message-list JS/CSS/templates
(HLD UX3a/UX3b/UX3d/UX3e/UX3f/UX3g/UX3h).

Regression guards for the message-list interaction stack:

1. The overflow ("more actions") menu's innerHTML used to be wiped on the
   first close: ``renderOverflowMenu`` restored from an
   ``originalContent`` cache that was only captured by the folder picker.
   The cache must be captured on first open, and the restore must no-op
   when uncaptured.
2. The active row must be elevated above sibling rows' revealed surfaces:
   the overflow menu's z-50 is confined to the action overlay's z-20
   stacking context, so without the row-level z-index, neighboring rows
   paint over the open menu.
3. U24.1/bug fix: ``lr-hit`` hit-area expansion must be scoped to
   coarse-pointer (touch) contexts. Unscoped, the star's expanded hit
   area overlapped the space right of the select checkbox on desktop,
   so clicks there toggled the star instead of the checkbox.
4. UX3g swipe gestures and UX3h long-press selection mode: the JS
   constants/disambiguation thresholds, the selection-mode navigation
   interception, and the template plumbing (swipe panels, foreground,
   circles, no "..." toggle) must all stay wired.
5. Mobile regression guards: swipe panels must span the full row width
   so the revealed background never shows a blank gap past the panel's
   content width (UX3g); every select-all entry point must enter
   selection mode on touch so the per-row circles appear (UX3h); and
   the hover/focus action overlay must stay hidden in selection mode
   and on touch devices (UX3b — a tap that focuses a row's tabindex'd
   span must not reveal the overlay).
"""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
STATIC_DIR = APP_DIR / "static"
MAIL_TEMPLATES = APP_DIR / "modules" / "mail" / "templates"
MESSAGE_LIST_JS = STATIC_DIR / "js" / "mail" / "message-list.js"
SWIPE_JS = STATIC_DIR / "js" / "mail" / "swipe.js"
BULK_SELECT_JS = STATIC_DIR / "js" / "mail" / "bulk-select.js"
TAILWIND_SRC_CSS = STATIC_DIR / "css" / "tailwind.src.css"
TAILWIND_BUILT_CSS = STATIC_DIR / "css" / "tailwind.css"

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _css_files():
    return [TAILWIND_SRC_CSS, TAILWIND_BUILT_CSS]


def _normalized(path):
    return "".join(_CSS_COMMENT.sub(" ", path.read_text()).split())


# --- Overflow menu cache guards (unchanged behavior) ---


def test_overflow_menu_restore_is_guarded_against_uncaptured_cache():
    source = MESSAGE_LIST_JS.read_text()
    # renderOverflowMenu must no-op when the cache was never captured
    # (restoring '' wipes the menu permanently).
    assert "if (!menu || !menu.dataset.originalContent) return;" in source
    assert "menu.dataset.originalContent || ''" not in source


def test_overflow_menu_content_is_captured_on_first_open():
    source = MESSAGE_LIST_JS.read_text()
    assert "menu.dataset.originalContent = menu.innerHTML;" in source


def test_inline_actions_release_stale_open_overflow_menu():
    """mark/lock hide the menu directly; a stale openOverflowMenu made the
    next "more actions" tap a silent no-op (open branch never ran)."""
    source = MESSAGE_LIST_JS.read_text()
    assert "if (openOverflowMenu === overflowMenu) {" in source
    assert source.count("openOverflowMenu = null;") >= 3


# --- Row elevation guards ---


def test_active_row_is_elevated_above_sibling_surfaces():
    for css in _css_files():
        normalized = _normalized(css)
        assert ".message-row.is-actions-open,.message-row[data-overlay-locked]{" in normalized, (
            f"row elevation rule missing from {css.name}"
        )
        assert "z-index:35" in normalized, css.name


def test_active_row_z_index_stays_below_header_and_drawers():
    """35 must stay above sibling rows' surfaces but below the sticky
    header/toasts (z-40) and drawers (z-50)."""
    for css in _css_files():
        assert "z-index:35" in _normalized(css)


# --- Bug fix: lr-hit expansion must be coarse-pointer-only (U24.1) ---


def test_lr_hit_expansion_is_scoped_to_coarse_pointers():
    """The star's expanded hit area used to overlap the space right of the
    select checkbox on desktop (fine pointer), hijacking clicks. The
    ::before expansion must live inside @media (pointer: coarse)."""
    for css in _css_files():
        # The minifier collapses `::before` to `:before` in the built file.
        normalized = _normalized(css).replace("::", ":")
        assert "@media(pointer:coarse)" in normalized, (
            f"coarse-pointer media query missing from {css.name}"
        )
        # Exactly one .lr-hit::before rule, and it must sit inside the
        # coarse-pointer block (its declaration follows the media query).
        assert normalized.count(".lr-hit:before{") == 1, css.name
        media_at = normalized.find("@media(pointer:coarse)")
        rule_at = normalized.find(".lr-hit:before{")
        assert 0 <= media_at < rule_at, (
            f".lr-hit::before must be scoped inside @media (pointer: coarse) in {css.name}"
        )


def test_locked_overlay_no_longer_shifts_actions_for_removed_toggle():
    """The "..." toggle is gone (UX3g); the padding rule that shifted the
    revealed actions clear of it must be gone too, otherwise the overlay
    wastes its right edge."""
    for css in _css_files():
        assert "padding-right:3rem!important" not in _normalized(css), css.name


# --- UX3g: swipe gestures ---


def test_swipe_panel_buttons_activate_on_touchend_fast_path():
    """Bug fix: Chrome can retarget a tap's click to the row even when
    every touch event resolved to the revealed panel button, so the
    delegated overflow-toggle/submit handlers never fired ("tapping the
    buttons does nothing"). The touchend fast-path must preventDefault
    the ghost click and click the panel button directly."""
    source = SWIPE_JS.read_text()
    assert "e.target.closest('[data-swipe-panel] button')" in source
    assert "panelButton.click()" in source


def test_swipe_translates_clip_wrapper_not_inner_foreground():
    """Bug fix: the swipe translated [data-row-foreground] inside the
    clip wrapper (``relative z-10 overflow-hidden``), whose box still
    covered the whole row above the panels (z-0) — revealed buttons
    could never receive taps. The gesture must translate the wrapper
    itself ([data-row-clip]) so the exposed area belongs to the panel."""
    source = SWIPE_JS.read_text()
    assert "row.querySelector('[data-row-clip]')" in source
    for name in ("message_list.html", "search.html", "search_full.html"):
        content = (MAIL_TEMPLATES / name).read_text()
        assert "data-row-clip" in content, f"data-row-clip missing from {name}"
    for css in _css_files():
        normalized = _normalized(css)
        assert ".message-row[data-row-clip]{transition:transform" in normalized, css.name


def test_swipe_constants_and_disambiguation_present():
    source = SWIPE_JS.read_text()
    assert "SWIPE_ENGAGE_PX = 12" in source
    assert "SWIPE_TRIGGER_PX = 60" in source
    # Horizontal must dominate vertical movement (U12.56d pattern).
    assert "Math.abs(dx) >= Math.abs(dy) * 1.5" in source
    # Swipe is disabled on desktop-width viewports.
    assert "matchMedia('(max-width: 1023.98px)')" in source
    # And while the list is in long-press selection mode (UX3h).
    assert "data-selection-mode" in source


def test_full_swipe_submits_archive_through_shared_handler():
    source = SWIPE_JS.read_text()
    assert "[data-swipe-archive-form]" in source
    assert "new Event('submit', { bubbles: true, cancelable: true })" in source


def test_swipe_module_is_loaded_by_all_list_pages():
    """Every page that inits LRMessageList must also load swipe.js (the
    swipe engine was extracted so message-list.js stays small)."""
    for name in ("folder.html", "search.html", "search_full.html"):
        content = (MAIL_TEMPLATES / name).read_text()
        assert "js/mail/swipe.js" in content, f"swipe.js missing from {name}"


def test_swipe_resets_after_inplace_actions():
    """mark/lock succeed in place; the swiped-open row must snap closed."""
    source = MESSAGE_LIST_JS.read_text()
    assert source.count("resetSwipeRow(row);") >= 2


# --- UX3g: swipe panel labels + "More" bottom sheet ---


def test_swipe_panel_buttons_carry_visible_labels():
    """UX3g: revealed quick actions must be self-explanatory — every tile
    carries a visible i18n'd label under its icon. Icon-only reveals are
    not acceptable (nobody knows what the icons do)."""
    content = (MAIL_TEMPLATES / "_row_common.html").read_text()
    right_at = content.find('data-swipe-panel="right"')
    assert right_at != -1, "right swipe panel missing from _row_common.html"
    panel = content[right_at:]
    for label in ("_('Archive')", "_('Delete')", "_('Mark read')", "_('Mark unread')", "_('More')"):
        assert label in panel, f"{label} label missing from the swipe panel"
    # Four labeled tiles (archive, delete, mark, more).
    assert panel.count("text-[10px] font-medium leading-none") == 4


def test_overflow_menu_module_is_loaded_by_all_list_pages():
    """Every page that inits LRMessageList must also load overflow_menu.js
    (the dropdown folder picker and the touch bottom sheet live there)."""
    for name in ("folder.html", "search.html", "search_full.html"):
        content = (MAIL_TEMPLATES / name).read_text()
        assert "js/mail/overflow_menu.js" in content, f"overflow_menu.js missing from {name}"


def test_swipe_panel_more_opens_bottom_sheet_not_hidden_dropdown():
    """The swipe panel's "More" must open the touch bottom sheet: the
    in-row dropdown lives inside the hover action overlay, which is
    display:none on touch, so opening it there shows nothing."""
    source = MESSAGE_LIST_JS.read_text()
    assert "overflowToggle.closest('[data-swipe-panel]')" in source
    assert "openSheet" in source


def test_sheet_submits_row_forms_instead_of_cloned_ones():
    """Sheet entries must submit the row's real overflow-menu forms (the
    shared inline-action path runs); a native submit of the cloned form
    would full-page navigate."""
    source = (STATIC_DIR / "js" / "mail" / "overflow_menu.js").read_text()
    assert "requestSubmit" in source
    assert "form.message-action[data-action=" in source


def test_sheet_dismissal_paths_present():
    source = (STATIC_DIR / "js" / "mail" / "overflow_menu.js").read_text()
    for needle in ("data-sheet-backdrop", "data-sheet-cancel", "e.key !== 'Escape'"):
        assert needle in source, f"{needle} missing from overflow_menu.js"


def test_sheet_css_and_reduced_motion_present():
    for css in _css_files():
        normalized = _normalized(css)
        assert ".lr-sheet.is-open.lr-sheet-backdrop{" in normalized, css.name
        assert ".lr-sheet.is-open.lr-sheet-panel{" in normalized, css.name
        assert ".lr-sheet-panel{transform:translateY(100%)" in normalized, css.name
        # The slide must be disabled under prefers-reduced-motion. (The
        # minifier merges same-predicate media blocks and reorders/splits
        # rule selectors, so this is an existence check, not positional.)
        assert "@media(prefers-reduced-motion:reduce)" in normalized, css.name
        assert ".lr-sheet-panel{transition:none" in normalized, css.name


# --- UX3h: long-press selection mode ---


def test_selection_mode_intercepts_row_navigation():
    source = MESSAGE_LIST_JS.read_text()
    assert "container.hasAttribute('data-selection-mode')" in source
    # The event must bubble: LRBulkSelect listens on the container.
    assert "new CustomEvent('lr:toggle-select', { bubbles: true })" in source


def test_long_press_constants_and_haptics_present():
    source = BULK_SELECT_JS.read_text()
    assert "LONG_PRESS_MS = 500" in source
    assert "TAP_SLOP_PX = 10" in source
    assert "navigator.vibrate" in source
    # Long-press is a touch-only, below-md accelerator.
    assert "matchMedia('(max-width: 767.98px)')" in source


def test_selection_mode_enter_exit_wiring():
    source = BULK_SELECT_JS.read_text()
    assert "container.setAttribute('data-selection-mode', '1')" in source
    assert "container.removeAttribute('data-selection-mode')" in source
    # Deselecting the last row exits selection mode.
    assert "selection.size === 0" in source
    assert "data-bulk-done" in source
    assert "data-bulk-select-page" in source


def test_selection_state_reapplied_after_list_replacement():
    """Bug fix: the folder view's background SSE sync replaces the whole
    list; replacement rows rendered from server HTML lose the selection
    visuals (circles/checkboxes) while the id Set lives on — a long-press
    racing a sync left the held row looking unselected. bulk-select must
    re-apply its state when the container's children are replaced."""
    source = BULK_SELECT_JS.read_text()
    assert "MutationObserver" in source
    assert "resyncOnListReplacement.observe(container, { childList: true })" in source


def test_selection_circle_css_present():
    for css in _css_files():
        normalized = _normalized(css)
        assert ".row-select-circle{" in normalized, css.name
        assert '[data-selection-mode="1"].row-select-circle' in normalized, css.name
        assert ".row-select-circle.is-selected{" in normalized, css.name


# --- Create-folder submit handler (U5.4a regression) ---


def test_create_folder_form_serializes_before_disabling_input():
    """The submit handler disabled the name input before building
    ``FormData``; disabled controls are excluded from FormData per the
    HTML spec, so ``name`` never reached the server and every create
    failed with "Folder name is required." The form data must be
    captured before any input is disabled."""
    source = (MAIL_TEMPLATES / "folder.html").read_text()
    start = source.find("createFolderForm.addEventListener('submit'")
    assert start != -1, "create-folder submit handler missing from folder.html"
    end = source.find("fetch(createFolderForm.action", start)
    assert end != -1, "create-folder fetch call missing from folder.html"
    handler = source[start:end]
    assert "new FormData(createFolderForm)" in handler
    disable_at = handler.find("createFolderInput.setAttribute('disabled'")
    formdata_at = handler.find("new FormData(createFolderForm)")
    if disable_at != -1:
        assert formdata_at < disable_at, (
            "FormData must be built before the name input is disabled; "
            "disabled controls are excluded from FormData and the server "
            "rejects the empty name."
        )


# --- Template plumbing ---


def test_message_row_toggle_removed_everywhere():
    for name in ("message_list.html", "search.html", "search_full.html", "_row_common.html"):
        content = (MAIL_TEMPLATES / name).read_text()
        assert "data-message-actions-toggle" not in content, name


def test_row_common_has_swipe_panels_and_selection_controls():
    content = (MAIL_TEMPLATES / "_row_common.html").read_text()
    assert 'data-swipe-panel="left"' in content
    assert 'data-swipe-panel="right"' in content
    assert "data-swipe-archive-form" in content
    assert "data-select-circle" in content
    assert 'aria-pressed="false"' in content


# --- Bug fix: swipe panels span the full row width (UX3g) ---


def test_swipe_panels_span_full_row_width():
    """Revealed panels used to be only content-width, so dragging past
    the panel's natural width exposed blank page background behind the
    row ("the green background stops"). Both panels must carry w-full so
    the colored fill always covers the entire revealed strip."""
    content = (MAIL_TEMPLATES / "_row_common.html").read_text()
    for panel in ('data-swipe-panel="left"', 'data-swipe-panel="right"'):
        idx = content.find(panel)
        assert idx != -1, f"{panel} missing from _row_common.html"
        div_start = content.rfind("<div", 0, idx)
        div = content[div_start:idx]
        assert "w-full" in div, f"{panel} div must carry w-full"


# --- Bug fix: select-all enters selection mode on touch (UX3h) ---


def test_select_all_enters_selection_mode_on_touch():
    """Tapping the header "Select" checkbox on mobile filled the
    selection but never entered selection mode, so the per-row circles
    and Done control stayed hidden. Every select-all entry point must
    call ensureSelectionMode(), which is gated to touch (below md)."""
    source = BULK_SELECT_JS.read_text()
    assert "function ensureSelectionMode()" in source
    assert "longPressQuery.matches" in source
    select_all_at = source.find("selectAll.addEventListener")
    assert select_all_at != -1
    assert (
        "ensureSelectionMode();"
        in source[select_all_at : source.find("}", source.find("refresh();", select_all_at))]
    )
    match_at = source.find("matchBtn.addEventListener")
    assert match_at != -1
    assert (
        "ensureSelectionMode();"
        in source[match_at : source.find("}", source.find("refresh();", match_at))]
    )


# --- Bug fix: action overlay hidden in selection mode and on touch (UX3b) ---


def test_action_overlay_hidden_in_selection_mode_and_on_touch():
    """The overlay's group-focus-within reveal fired on touch (tapping a
    row focuses its tabindex'd span), showing Archive/Delete/... during
    selection. The overlay must be display:none while the list is in
    selection mode, and on hover-incapable/coarse-pointer devices."""
    for css in _css_files():
        normalized = _normalized(css)
        assert '[data-selection-mode="1"].message-actions-overlay{display:none' in normalized, (
            css.name
        )
        media_at = normalized.find("@media(hover:none),(pointer:coarse)")
        assert media_at != -1, f"touch media query missing from {css.name}"
        rule_at = normalized.find(".message-actions-overlay{display:none", media_at)
        assert rule_at != -1, (
            f".message-actions-overlay display:none must sit inside the (hover: none), (pointer: coarse) block in {css.name}"
        )


def test_message_rows_use_responsive_single_copy_layout():
    """UX3a/UX3e: the sender/date/subject triplet reflows between the
    desktop single-line row and the mobile three-line row from one copy
    of the markup (flex-wrap + order), and the snippet is mobile-only."""
    content = (MAIL_TEMPLATES / "message_list.html").read_text()
    assert "data-row-foreground" in content
    assert "basis-full md:basis-auto" in content
    assert "md:flex-1 md:order-2" in content
    assert "md:order-3" in content
    # Fixed-width sender column on desktop (UX3a).
    assert "md:w-36 lg:w-44 xl:w-52" in content
    # Mobile-only snippet line (UX3e) — desktop rows are single-line.
    assert (
        'class="md:hidden mt-1 text-[13px] text-slate-600 truncate" data-snippet="true"' in content
    )
    # Two star positions: desktop column + mobile under the date.
    assert content.count("{{ star_form(") == 2
