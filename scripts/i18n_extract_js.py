"""Supplement the gettext catalog with JS-side translation keys.

``pybabel extract`` only covers Python ``_()`` and Jinja ``_()`` calls. The
browser-side catalog (HLD U26.6c) uses ``window.LR.t('...')`` / ``LR.t('...')``
in static JS files and inline <script> blocks inside templates. This script
scans those, deduplicates against the existing ``messages.pot``, and appends
any missing msgids so ``pybabel update`` keeps them in sync.

Usage (run after ``pybabel extract``, before ``pybabel update/init``):

    ./venv/bin/python scripts/i18n_extract_js.py
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POT = REPO / "app" / "translations" / "messages.pot"

SCAN_ROOTS = [
    REPO / "app" / "static" / "js",
    REPO / "app" / "modules",
    REPO / "app" / "templates",
]

# window.LR.t('...') / LR.t('...') with single or double quotes.
# Keys may contain {placeholder} tokens and escaped quotes.
CALL_RE = re.compile(
    r"""(?:window\.)?LR\.t\(\s*(['"])((?:\\.|(?!\1).)*)\1""",
    re.DOTALL,
)


def unescape(literal: str) -> str:
    return (
        literal.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    )


def existing_msgids(pot_text: str) -> set[str]:
    return set(re.findall(r'^msgid "((?:[^"\\]|\\.)*)"', pot_text, re.MULTILINE))


def unquote_po(msgid: str) -> str:
    # msgids in the pot use gettext escaping (\" \n \\) — compare consistently.
    return msgid.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def main() -> int:
    if not POT.exists():
        print(f"error: {POT} not found — run pybabel extract first", file=sys.stderr)
        return 1

    pot_text = POT.read_text(encoding="utf-8")
    have = {unquote_po(m) for m in existing_msgids(pot_text)}

    found: list[str] = []
    seen: set[str] = set()
    for root in SCAN_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in (".js", ".html"):
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for _match, literal in CALL_RE.findall(text):
                key = unescape(literal)
                if not key or key in seen:
                    continue
                seen.add(key)
                if key not in have:
                    found.append(key)

    if not found:
        print("no new JS keys")
        return 0

    def po_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    lines = ["\n# JS-side keys (scripts/i18n_extract_js.py)\n"]
    for key in sorted(found):
        lines.append(f'msgid "{po_escape(key)}"\nmsgstr ""\n\n')
    with POT.open("a", encoding="utf-8") as fh:
        fh.writelines(lines)
    print(f"appended {len(found)} JS keys to {POT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
