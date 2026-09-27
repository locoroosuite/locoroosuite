"""Static-asset guards for mail message-list JS/CSS (HLD UX3b).

Regression guards for the mobile "..." row-action flow:

1. The overflow ("more actions") menu's innerHTML used to be wiped on the
   first close: ``renderOverflowMenu`` restored from an ``originalContent``
   cache that was only captured by the folder picker, so ``innerHTML``
   became '' and every later open showed an empty menu. The cache must be
   captured on first open, and the restore must no-op when uncaptured.
2. The active row must be elevated above sibling rows' "..." toggles
   (z-30): the overflow menu's z-50 is confined to the action overlay's
   z-20 stacking context, so without the row-level z-index, neighboring
   rows' toggles painted over the open menu.
3. The "..." toggle must stay visible while the overlay is locked
   (reveal/hide per HLD UX3b) and the revealed actions must shift clear
   of it on mobile instead of hiding the toggle via display:none.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"
MESSAGE_LIST_JS = STATIC_DIR / "js" / "mail" / "message-list.js"
TAILWIND_SRC_CSS = STATIC_DIR / "css" / "tailwind.src.css"
TAILWIND_BUILT_CSS = STATIC_DIR / "css" / "tailwind.css"

OLD_TOGGLE_HIDE_SELECTOR = ".message-row[data-overlay-locked] [data-message-actions-toggle]"

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _css_files():
    return [TAILWIND_SRC_CSS, TAILWIND_BUILT_CSS]


def _normalized(path):
    return "".join(_CSS_COMMENT.sub(" ", path.read_text()).split())


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


def test_active_row_is_elevated_above_sibling_toggles():
    for css in _css_files():
        normalized = _normalized(css)
        assert ".message-row.is-actions-open,.message-row[data-overlay-locked]{" in normalized, (
            f"row elevation rule missing from {css.name}"
        )
        assert "z-index:35" in normalized, css.name


def test_active_row_z_index_stays_below_header_and_drawers():
    """35 must stay above sibling rows' z-30 toggles but below the sticky
    header/toasts (z-40) and drawers (z-50)."""
    for css in _css_files():
        assert "z-index:35" in _normalized(css)


def test_locked_overlay_keeps_toggle_visible_and_shifts_actions():
    for css in _css_files():
        normalized = _normalized(css)
        assert OLD_TOGGLE_HIDE_SELECTOR.replace(" ", "") not in normalized, (
            f"locked overlay must not display:none the '...' toggle ({css.name})"
        )
    # Actions shift clear of the still-visible toggle below lg.
    assert "padding-right:3rem!important" in _normalized(TAILWIND_BUILT_CSS)
    assert "padding-right: 3rem !important" in TAILWIND_SRC_CSS.read_text()
