"""Static guards for chat JS banner semantics.

The chat banner (``#chat-banner``) must render feedback in the app-wide
semantic colors: amber for warnings/errors, emerald for successes (matching
the ``window.LR.notifySuccess`` toast in layout.html). A regression shipped
once: "Conversation started with …" was amber because the banner template
hardcoded warning colors for every message — these tests keep success green.
"""

import re
from pathlib import Path

CHAT_DIR = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "chat"
TEMPLATE = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "chat" / "templates" / "chat" / "index.html"
)


def test_banner_defines_literal_color_sets_for_both_kinds():
    core = (CHAT_DIR / "chat-core.js").read_text()
    # Literal class sets — Tailwind only emits classes it sees as literals.
    assert "border-amber-200 bg-amber-50 text-amber-800" in core
    assert "border-emerald-200 bg-emerald-50 text-emerald-800" in core
    # The kind switch must exist.
    assert re.search(r'kind === "success" \? BANNER_SUCCESS : BANNER_WARNING', core)


def test_success_call_sites_request_success_kind():
    ui = (CHAT_DIR / "chat-ui.js").read_text()
    for fragment in ('"Conversation started with {peer}{matrixId}"', '"Invited {email}{userId}"'):
        m = re.search(re.escape(fragment) + r".*?", ui, re.DOTALL)
        assert m, f"missing call site: {fragment}"
        tail = ui[m.start() : m.start() + 500]
        assert '"success"' in tail, f"call site {fragment} must pass kind 'success'"


def test_banner_template_has_no_hardcoded_color():
    html = TEMPLATE.read_text()
    m = re.search(r'<div id="chat-banner" class="([^"]*)"', html)
    assert m, "chat-banner element missing"
    assert "amber" not in m.group(1)
    assert "emerald" not in m.group(1)
