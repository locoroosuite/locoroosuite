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
    assert 'container.setAttribute(\'data-selection-mode\', \'1\')' in source
    assert "container.removeAttribute('data-selection-mode')" in source
    # Deselecting the last row exits selection mode.
    assert "selection.size === 0" in source
    assert "data-bulk-done" in source
    assert "data-bulk-select-page" in source


def test_selection_circle_css_present():
    for css in _css_files():
        normalized = _normalized(css)
        assert ".row-select-circle{" in normalized, css.name
        assert '[data-selection-mode="1"].row-select-circle' in normalized, css.name
        assert ".row-select-circle.is-selected{" in normalized, css.name


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
    assert 'class="md:hidden mt-1 text-[13px] text-slate-600 truncate" data-snippet="true"' in content
    # Two star positions: desktop column + mobile under the date.
    assert content.count("{{ star_form(") == 2
