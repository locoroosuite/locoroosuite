"""UX3b/UX3d guards on the precompiled Tailwind stylesheet.

The accidental-archive bug on mobile (HLD UX3b): browsers emulate :hover
during a tap, which made the invisible action overlay interactive. The fix
gates all hover:/group-hover: variants behind a device-capability media
query (Tailwind ``future.hoverOnlyWhenSupported``). These tests fail if a
config/CSS rebuild drops that guard.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPILED = _REPO_ROOT / "app" / "static" / "css" / "tailwind.css"
_CONFIG = _REPO_ROOT / "tailwind.config.js"
_SRC = _REPO_ROOT / "app" / "static" / "css" / "tailwind.src.css"


def _compiled_css() -> str:
    assert _COMPILED.exists(), "compiled tailwind.css is missing (run `make css`)"
    return _COMPILED.read_text()


def test_hover_pointer_events_gated_by_capability():
    """Every .group:hover pointer-events-auto rule must sit inside the
    (hover: hover) and (pointer: fine) media guard (UX3b)."""
    css = _compiled_css()
    selector = ".group:hover .group-hover\\:pointer-events-auto"
    matches = list(re.finditer(re.escape(selector), css))
    assert matches, "group-hover:pointer-events-auto utility missing from compiled CSS"
    for match in matches:
        window = css[max(0, match.start() - 150) : match.start()]
        assert "(hover:hover) and (pointer:fine)" in window, (
            "hover variants are not wrapped in (hover:hover) and (pointer:fine) -- "
            "is future.hoverOnlyWhenSupported still set in tailwind.config.js and "
            "the CSS rebuilt with `make css`?"
        )


def test_config_enables_hover_only_when_supported():
    config = _CONFIG.read_text()
    assert "hoverOnlyWhenSupported: true" in config, (
        "tailwind.config.js must keep future.hoverOnlyWhenSupported enabled (UX3b)"
    )


def test_touch_visible_fallback_in_compiled_css():
    """UX3d: hover-hidden actions are always visible on (hover: none) devices."""
    css = _compiled_css()
    assert "(hover:none)" in css, "(hover:none) media rule missing from compiled CSS"
    assert ".lr-touch-visible" in css, ".lr-touch-visible fallback missing from compiled CSS"
    idx = css.find(".lr-touch-visible")
    window = css[max(0, idx - 120) : idx]
    assert "(hover:none)" in window, ".lr-touch-visible must live inside @media (hover: none)"


def test_src_css_documents_the_pattern():
    src = _SRC.read_text()
    assert ".lr-touch-visible" in src
    assert ".lr-hit" in src
