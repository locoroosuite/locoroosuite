"""Static guards for the shared edge-swipe drawer gesture (HLD U24.2a).

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
CAL_MAIN = ROOT / "app" / "static" / "js" / "calendar" / "main.js"
DOCS_LIST = ROOT / "app" / "modules" / "docs" / "templates" / "docs_list.html"


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
        assert "LRDrawer.register" in DOCS_LIST.read_text()

    def test_mail_row_swipe_yields_edge_touches(self):
        assert "LRDrawer.claims" in MAIL_SWIPE.read_text(), (
            "mail row swipe (UX3g) must yield edge-origin touches (U24.2a) "
            "or an edge drag archives a message while opening the drawer"
        )
