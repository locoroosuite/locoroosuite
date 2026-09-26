"""Static-asset guards for calendar JS (U12.56h).

Tailwind's JIT scanner only emits utility classes that appear as string
literals in the scanned files (templates + app/static/js). Building an
arbitrary-value class at runtime (string concatenation) produces a class that
silently does not exist in the compiled CSS — the week view shipped with
`grid-cols-[36px_repeat(7,...)]` constructed this way and collapsed to a
single stacked column. These tests keep that bug class out of the codebase.
"""

import re
from pathlib import Path

STATIC_JS_DIR = Path(__file__).resolve().parents[2] / "app" / "static" / "js"

# An arbitrary-value class being *opened* from a variable, e.g.
# 'grid-cols-[' + gutter — the value can never be a scanner-visible literal.
DYNAMIC_ARBITRARY_CLASS = re.compile(r"-\[\s*'\s*\+")


def _js_files():
    return sorted(STATIC_JS_DIR.glob("**/*.js"))


def test_static_js_has_no_dynamic_tailwind_arbitrary_classes():
    offenders = []
    for path in _js_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if DYNAMIC_ARBITRARY_CLASS.search(line):
                offenders.append(f"{path.relative_to(STATIC_JS_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Tailwind arbitrary-value classes must be literals (or inline styles). "
        "Dynamic construction found:\n" + "\n".join(offenders)
    )


def test_time_grid_uses_inline_grid_template_not_classes():
    """The time grid template must come from inline grid-template-columns."""
    source = (STATIC_JS_DIR / "calendar" / "time_grid.js").read_text()
    assert "grid-template-columns:" in source
    assert "grid-cols-[" not in source
