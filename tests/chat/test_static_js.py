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
    Path(__file__).resolve().parents[2]
    / "app"
    / "modules"
    / "chat"
    / "templates"
    / "chat"
    / "index.html"
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


def test_room_filter_is_wired_end_to_end():
    """HLD U7.11/U25.12: the header search box filters rooms client-side.

    The contract spans two files: search-panel.js dispatches ``lr:search-live``
    for live-mode forms (and blocks submit), chat-ui.js listens for it and
    chat-core.js implements the filtering + "no rooms match" empty states.
    """
    shell = (
        Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "mail" / "search-panel.js"
    ).read_text()
    assert "lr:search-live" in shell
    assert "data-search-live" in shell
    assert "preventDefault" in shell

    ui = (CHAT_DIR / "chat-ui.js").read_text()
    assert re.search(r'addEventListener\("lr:search-live"', ui)
    assert "Chat.setRoomFilter" in ui

    core = (CHAT_DIR / "chat-core.js").read_text()
    assert "setRoomFilter: setRoomFilter" in core
    # DMs match the peer's email too, not just the display name.
    assert "peer.email" in core
    # Filter-active empty states replace the default list-empty messages.
    assert '"No rooms match your filter."' in core
    assert '"No conversations match your filter."' in core


def test_calls_script_is_versioned_and_wired():
    """HLD U25.19-U25.23: chat-calls.js ships with cache-busted URLs and the
    signaling hooks are wired through core (bootstrap), ui (SSE dispatch)."""
    html = TEMPLATE.read_text()
    assert re.search(
        r"chat-calls\.js'\s*\}\)\?\?v=\{\{\s*static_v\(\"js/chat/chat-calls\.js\"\)", html
    ) or re.search(r'static_v\("js/chat/chat-calls\.js"\)', html)

    calls = (CHAT_DIR / "chat-calls.js").read_text()
    core = (CHAT_DIR / "chat-core.js").read_text()
    ui = (CHAT_DIR / "chat-ui.js").read_text()
    # SSE → calls dispatch lives in ui, bootstrap dispatch in core, both guarded.
    assert "Chat.calls.handleEvents" in ui
    assert "window.Chat.calls" in core
    # Key call strings go through i18n, never hardcoded at render sites.
    for key in ("Ringing…", "Missed call", "Incoming video call", "The call ended."):
        assert f'window.LR.t("{key}")' in calls, f"missing LR.t for: {key}"


def test_calls_overlay_elements_exist():
    """The call UI skeleton (HLD U25.22) is template-authored so Tailwind
    sees the classes as literals; JS only toggles visibility."""
    html = TEMPLATE.read_text()
    for element_id in (
        "chat-call-overlay",
        "chat-call-incoming",
        "chat-call-active",
        "chat-call-accept",
        "chat-call-decline",
        "chat-call-hangup",
        "chat-call-mute",
        "chat-call-camera",
        "chat-call-remote-video",
        "chat-call-local-video",
    ):
        assert f'id="{element_id}"' in html, f"missing call overlay element: {element_id}"


def test_device_test_dialog_and_script_are_wired():
    """HLD U25.59: the test-call dialog skeleton is template-authored (Tailwind
    literals), the script is cache-busted, the mic meter width uses an inline
    style (never runtime-built Tailwind classes), and user-facing strings go
    through window.LR.t."""
    html = TEMPLATE.read_text()
    assert 'static_v("js/chat/chat-device-test.js")' in html
    for element_id in (
        "chat-test-call",
        "chat-test-call-dialog",
        "chat-test-preview",
        "chat-test-mic-bar",
        "chat-test-sound",
        "chat-test-loop",
        "chat-test-remote",
        "chat-test-unmute",
        "chat-test-close",
    ):
        assert f'id="{element_id}"' in html, f"missing device-test element: {element_id}"

    # The meter bar's dynamic width must be an inline style, not a class.
    m = re.search(r'id="chat-test-mic-bar"[^>]*style="([^"]*)"', html)
    assert m and "width" in m.group(1), "mic bar must size via inline style"

    js = (CHAT_DIR / "chat-device-test.js").read_text()
    assert "style.width" in js
    assert ".className" not in js, "runtime className writes bypass the Tailwind JIT"
    for key in (
        "Direct connection: working",
        "Direct connection: failed",
        "TURN relay: working",
        "TURN relay: unreachable",
        "Voice/video calls are disabled for your domain (no TURN server configured).",
        "Could not start the test call: {error}",
    ):
        # Prefix match: calls may pass interpolation args after the key.
        assert f'window.LR.t("{key}"' in js, f"missing LR.t for: {key}"

    # Loopback never uses Matrix signaling (U25.23/U25.59): no call endpoints.
    assert "/app/chat/api/rooms/" not in js


def test_media_error_banner_links_to_device_test():
    """HLD U25.59: chat-core banner supports an inline action and both real
    call getUserMedia failures open the device-test dialog from it."""
    core = (CHAT_DIR / "chat-core.js").read_text()
    assert "action.label" in core
    assert "action.fn" in core
    # Literal classes for the inline link (Tailwind JIT scans static JS).
    assert "ml-2 underline font-semibold" in core

    calls = (CHAT_DIR / "chat-calls.js").read_text()
    assert calls.count("testDevicesAction()") >= 2
    assert 'window.LR.t("Test your devices")' in calls
