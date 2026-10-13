"""Static guard: customer-facing font sizes must be rem-based (HLD UX8c).

The per-user text scale works by scaling the root font-size, which only
affects rem-based utilities. A `text-[12px]` class freezes that text at 12px
for every scale — and also opts it out of the browser's font-size preference
and the UX8 phone-viewport bump. These tests keep px font-size utilities out
of customer-facing templates/JS so text scales uniformly everywhere.

Standalone, self-styled documents (docs editor/share pages) and the admin
back office (U24.36, desktop-first) are out of scope.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_DIRS = [
    REPO_ROOT / "app" / "templates",
    REPO_ROOT / "app" / "modules" / "mail" / "templates",
    REPO_ROOT / "app" / "modules" / "contacts" / "templates",
    REPO_ROOT / "app" / "modules" / "calendar" / "templates",
    REPO_ROOT / "app" / "modules" / "docs" / "templates",
    REPO_ROOT / "app" / "modules" / "chat" / "templates",
    REPO_ROOT / "app" / "static" / "js",
]

# Standalone documents with their own stylesheet, rendered outside the
# scaled layout. Add a file here only with a deliberate reason.
ALLOWLIST: set[str] = set()

PX_TEXT_CLASS = re.compile(r"text-\[\d+(\.\d+)?px\]")


def _scan_files():
    for scan_dir in SCAN_DIRS:
        for path in sorted(scan_dir.glob("**/*")):
            if path.is_dir() or path.suffix not in {".html", ".js"}:
                continue
            if path.name in ALLOWLIST:
                continue
            yield path


def test_customer_facing_templates_have_no_px_text_utilities():
    offenders = []
    for path in _scan_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if PX_TEXT_CLASS.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Font-size utilities on customer-facing screens must be rem-based "
        "(e.g. text-[0.75rem], never text-[12px]) so the per-user text scale "
        "and browser font-size preference apply uniformly (HLD UX8c). "
        "Deliberate exceptions go in ALLOWLIST with a reason. Found:\n"
        + "\n".join(offenders)
    )
