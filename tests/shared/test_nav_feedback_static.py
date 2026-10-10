"""Static guards for the shared navigation pending feedback (HLD U24.39/UX9).

The nav-feedback engine (app/static/js/nav-feedback.js) must be loaded by
the shared layout head — module inline handlers call LR.navPending() at
event time, so the global must exist — and always through the cache-busting
static_v helper. The pending CSS components must exist in both the source
and the compiled stylesheet (Tailwind is not involved: the classes are
authored as CSS components, so no runtime-built class risk). Programmatic
form submits must not bypass feedback: switches use requestSubmit() +
navPending, fetch-driven row navigation calls navPending before
window.location.href, and no customer-facing docs flow may use alert()
(U24.39: failures surface as toasts).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LAYOUT = ROOT / "app" / "templates" / "layout.html"
NAV_JS = ROOT / "app" / "static" / "js" / "nav-feedback.js"
TAILWIND_SRC_CSS = ROOT / "app" / "static" / "css" / "tailwind.src.css"
TAILWIND_BUILT_CSS = ROOT / "app" / "static" / "css" / "tailwind.css"
MAIL_MESSAGE_LIST_JS = ROOT / "app" / "static" / "js" / "mail" / "message-list.js"
MAIL_FOLDER = ROOT / "app" / "modules" / "mail" / "templates" / "folder.html"
ACCOUNT_SWITCHER = ROOT / "app" / "templates" / "_account_switcher.html"
CAL_INDEX = ROOT / "app" / "modules" / "calendar" / "templates" / "index.html"
CAL_API = ROOT / "app" / "static" / "js" / "calendar" / "api.js"
DOCS_LIST = ROOT / "app" / "modules" / "docs" / "templates" / "docs_list.html"
DOCS_JS_DIR = ROOT / "app" / "static" / "js" / "docs"
CONTACTS_LIST = ROOT / "app" / "modules" / "contacts" / "templates" / "list.html"

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _css_text(path):
    return "".join(_CSS_COMMENT.sub(" ", path.read_text()).split())


class TestNavFeedbackStatic:
    def test_layout_loads_nav_feedback_js_versioned(self):
        html = LAYOUT.read_text()
        assert "static_v('js/nav-feedback.js')" in html, (
            "layout.html must reference js/nav-feedback.js with the static_v "
            "cache-busting helper (Static Asset Versioning)"
        )
        # Head placement: LR.navPending must exist before body handlers run.
        assert html.index("js/nav-feedback.js") < html.index("{% block content %}")

    def test_nav_js_guards_and_api(self):
        source = NAV_JS.read_text()
        # Public API used by modules.
        assert "window.LR.navPending = navPending" in source
        assert "window.LR.clearNavPending = clearNavPending" in source
        # In-page handlers that already claimed a click must keep their verdict.
        assert "if (e.defaultPrevented) return;" in source
        # Modified clicks, new tabs, and downloads are excluded.
        assert "e.metaKey || e.ctrlKey || e.shiftKey || e.altKey" in source
        assert "link.hasAttribute('download')" in source
        # bfcache restore must clear the stale dimmed state (UX9).
        assert "window.addEventListener('pageshow', clearNavPending)" in source
        # Safety timeout for aborted navigations.
        assert "CLEAR_TIMEOUT_MS" in source
        # Dimming is a CSS component authored as a literal class name
        # (no runtime-built Tailwind classes).
        assert "classList.add('lr-nav-pending')" in source
        assert "'lr-nav-progress'" in source

    def test_pending_css_components_exist_in_source_and_build(self):
        for css in (_css_text(TAILWIND_SRC_CSS), _css_text(TAILWIND_BUILT_CSS)):
            assert ".lr-nav-pending" in css
            assert ".lr-nav-progress-bar" in css
            assert "lr-nav-slide" in css
            # Reduced motion: instant dim, static bar (UX9).
            assert "prefers-reduced-motion" in css

    def test_message_rows_enter_pending_state_before_navigation(self):
        source = MAIL_MESSAGE_LIST_JS.read_text()
        assert "window.LR.navPending" in source
        assert source.index("window.LR.navPending") < source.index(
            "window.location.href = messageUrl"
        ), "UX9: the row must be acknowledged before the full-page navigation"

    def test_mail_folder_drawer_closes_on_link_tap_and_switch_uses_request_submit(self):
        source = MAIL_FOLDER.read_text()
        assert "window.LR.navPending(this); this.form.requestSubmit()" in source
        # UX9: mobile drawer closes before navigation proceeds.
        assert "setDrawerOpen(false);" in source
        assert "e.target.closest('a[href]')" in source

    def test_shared_account_switcher_uses_request_submit(self):
        source = ACCOUNT_SWITCHER.read_text()
        assert "window.LR.navPending(this); this.form.requestSubmit()" in source
        assert "this.form.submit()" not in source, (
            "U24.39: programmatic submit() bypasses the global submit spinner"
        )

    def test_calendar_checkbox_toggle_uses_request_submit(self):
        source = CAL_INDEX.read_text()
        assert "window.LR.navPending(this.closest('.group')); this.form.requestSubmit()" in source
        assert "this.form.submit()" not in source

    def test_calendar_fetch_errors_surface_as_toast(self):
        source = CAL_API.read_text()
        assert "notifyFetchError" in source
        assert "Failed to load calendar events." in source
        # A failed fetch must never be cached as an empty range (the toast
        # call site lives in the catch, after the success-path cache store).
        assert source.index("notifyFetchError();") > source.index("rangeCache[key] = data;")

    def test_docs_never_use_alert(self):
        # U13.33a moved the docs list inline script to static JS files; scan
        # both the template and the JS so a regression in either is caught.
        sources = [DOCS_LIST.read_text()] + [
            p.read_text() for p in sorted(DOCS_JS_DIR.glob("*.js"))
        ]
        for source in sources:
            assert not re.search(r"\balert\(", source), (
                "U24.39: docs failures surface as LR.notifyError toasts, never alert()"
            )
        assert any("window.LR.setButtonLoading(btn);" in source for source in sources)

    def test_contacts_delete_uses_shared_loading(self):
        source = CONTACTS_LIST.read_text()
        assert "window.LR.setButtonLoading(btn);" in source
        assert "form.requestSubmit()" in source
        assert "form.submit()" not in source
