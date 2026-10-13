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


def test_navigation_shows_busy_indicator_and_guards_stale_renders():
    """U24.39: view/date navigation surfaces the cal-busy indicator while
    events load, and a superseded fetch may not render a stale range."""
    source = (STATIC_JS_DIR / "calendar" / "state.js").read_text()
    assert "LRCal.setBusy(true);" in source
    assert "if (seq === navSeq) LRCal.setBusy(false);" in source
    assert "if (seq !== navSeq) return;" in source


def test_fetch_failures_never_cached_and_surface_as_toast():
    """U24.39: a failed events fetch resolves to [] for callers but is
    never cached as an empty range, and errors surface via a toast."""
    source = (STATIC_JS_DIR / "calendar" / "api.js").read_text()
    assert "notifyFetchError" in source
    cache_store = source.index("rangeCache[key] = data;")
    failure = source.index("notifyFetchError();")
    assert cache_store < failure


def _source(name):
    return (STATIC_JS_DIR / "calendar" / name).read_text()


def test_default_event_duration_is_a_shared_constant():
    """U12.56j: the 30-minute default duration has one source of truth in
    date_utils.js (loaded first) and no creation path hard-codes 60."""
    assert "LRCal.DEFAULT_EVENT_DURATION_MIN = 30;" in _source("date_utils.js")
    for name in ("editor.js", "dnd.js"):
        source = _source(name)
        assert "LRCal.DEFAULT_EVENT_DURATION_MIN" in source
        assert "startMin + 60" not in source
    # The Create button/FAB pass dtstart only; the editor derives the end.
    assert "'T10:00'" not in _source("main.js")


def test_editor_sync_end_from_start_is_duration_preserving():
    """U12.56j: both start fields feed syncEndFromStart (no stale-guard),
    the sync keeps multi-day end dates in place, and save clamps end
    <= start to the default duration."""
    source = _source("editor.js")
    assert "syncEndFromStart('date');" in source
    assert "syncEndFromStart('time');" in source
    # The old guard (sync only when end date empty) must not come back.
    assert "if (!el('ce-end-date').value) syncEndFromStart" not in source
    assert "wasSameDay" in source
    assert "deriveEnd(sd, st, LRCal.DEFAULT_EVENT_DURATION_MIN)" in source
    # Midnight rollover derives a real next-day date instead of wrapping.
    assert "Math.floor(total / 1440)" in source
