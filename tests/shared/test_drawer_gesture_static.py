"""Static guards for the shared edge-swipe drawer gesture (HLD U24.2a)
and the mobile logo drawer toggle (HLD U24.2b).

The gesture engine (app/static/js/drawer.js) must be loaded by the shared
layout head — module inline scripts call LRDrawer.register() during body
parsing, so the global must exist before them — and always through the
cache-busting static_v helper. Each drawer owner registers itself, and the
competing horizontal-swipe handlers (mail rows UX3g, calendar period nav
U12.56d) must yield edge-origin touches via LRDrawer.claims().
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LAYOUT = ROOT / "app" / "templates" / "layout.html"
DRAWER_JS = ROOT / "app" / "static" / "js" / "drawer.js"
MAIL_FOLDER = ROOT / "app" / "modules" / "mail" / "templates" / "folder.html"
MAIL_SWIPE = ROOT / "app" / "static" / "js" / "mail" / "swipe.js"
CAL_INDEX = ROOT / "app" / "modules" / "calendar" / "templates" / "index.html"
CAL_MAIN = ROOT / "app" / "static" / "js" / "calendar" / "main.js"
DOCS_LIST = ROOT / "app" / "modules" / "docs" / "templates" / "docs_list.html"
DOCS_LIST_JS = ROOT / "app" / "static" / "js" / "docs" / "doc-list.js"


class TestDrawerGestureStatic:
    def test_layout_loads_drawer_js_versioned(self):
        html = LAYOUT.read_text()
        assert "static_v('js/drawer.js')" in html, (
            "layout.html must reference js/drawer.js with the static_v "
            "cache-busting helper (Static Asset Versioning)"
        )
        # Head placement: module inline scripts register at parse time.
        assert html.index("js/drawer.js") < html.index("{% block content %}")

    def test_drawer_js_exposes_register_and_claims(self):
        source = DRAWER_JS.read_text()
        assert "window.LRDrawer" in source
        assert "register: register" in source
        assert "claims: claims" in source
        # Gesture math stays inline-style only (no runtime Tailwind classes).
        assert "classList.add('translate" not in source

    def test_mail_folder_registers_drawer(self):
        assert "LRDrawer.register" in MAIL_FOLDER.read_text()

    def test_calendar_registers_drawer_and_guards_period_swipe(self):
        source = CAL_MAIN.read_text()
        assert "LRDrawer.register" in source
        assert "LRDrawer.claims" in source, (
            "calendar period swipe (U12.56d) must yield edge-origin touches (U24.2a)"
        )

    def test_docs_registers_drawer(self):
        # U13.33a: the docs list drawer wiring lives in doc-list.js (the
        # inline script was extracted from the template).
        source = DOCS_LIST_JS.read_text()
        assert "LRDrawer.register" in source
        assert "LRDrawer.claims" in source, (
            "docs card swipe (U24.32) must yield edge-origin touches (U24.2a) "
            "or an edge drag trashes a document while opening the drawer"
        )

    def test_mail_row_swipe_yields_edge_touches(self):
        assert "LRDrawer.claims" in MAIL_SWIPE.read_text(), (
            "mail row swipe (UX3g) must yield edge-origin touches (U24.2a) "
            "or an edge drag archives a message while opening the drawer"
        )


class TestLogoDrawerToggleStatic:
    """U24.2b: below lg the header logo is the single mobile drawer
    control; the former content-header hamburgers are gone."""

    def test_layout_logo_carries_toggle_target_id(self):
        html = LAYOUT.read_text()
        assert 'id="brand-logo"' in html, (
            "layout.html's header logo must carry id=\"brand-logo\" so the "
            "shared drawer engine can claim its taps on mobile (U24.2b)"
        )

    def test_drawer_js_wires_logo_toggle(self):
        source = DRAWER_JS.read_text()
        assert "brand-logo" in source, (
            "drawer.js must own the logo toggle wiring (U24.2b) — it is "
            "loaded on every page via layout.html"
        )
        assert "toggle: toggle" in source
        assert "hasDrawer: hasDrawer" in source
        # Capture phase: the click must be claimed before nav-feedback's
        # (UX9) bubble-phase listener sees it, or a suppressed navigation
        # flashes pending state.
        assert "e.preventDefault()" in source
        assert "e.altKey" in source, "modified clicks must keep navigating"

    def test_content_header_hamburgers_removed(self):
        pairs = [
            (MAIL_FOLDER, "mobile-sidebar-toggle"),
            (CAL_INDEX, "cal-sidebar-toggle"),
            (CAL_MAIN, "cal-sidebar-toggle"),
            (DOCS_LIST, "docs-sidebar-toggle"),
            (DOCS_LIST_JS, "docs-sidebar-toggle"),
        ]
        for path, marker in pairs:
            assert marker not in path.read_text(), (
                f"{path.name} must not reference {marker}: the logo is the "
                "single mobile drawer control (U24.2b)"
            )
