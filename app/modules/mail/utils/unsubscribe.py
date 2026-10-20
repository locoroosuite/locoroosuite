"""List-Unsubscribe header parsing (RFC 2369) for the U8.10 affordance.

Body footers routinely carry sender-side markup bugs (double-rendered anchors
that corrupt their own href), so the unsubscribe affordance is sourced from
the transport header, which the sender's mail platform generates separately
from the body template.
"""

import re
from email.message import Message
from typing import Any
from urllib.parse import parse_qs, urlsplit

_HEADER_SEGMENT_RE = re.compile(r"<([^<>]*)>")
_MAX_URL_CHARS = 2048
_MAX_TO_CHARS = 320
_MAX_SUBJECT_CHARS = 998


def _unfold_header(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    return " ".join(str(value).split())


def _parse_mailto(segment: str) -> dict[str, str] | None:
    try:
        parts = urlsplit(segment)
    except ValueError:
        return None
    to_addr = (parts.path or "").strip()[:_MAX_TO_CHARS]
    if not to_addr or "@" not in to_addr:
        return None
    subject = ""
    if parts.query:
        values = parse_qs(parts.query, keep_blank_values=True).get("subject")
        if values and values[0]:
            subject = values[0][:_MAX_SUBJECT_CHARS]
    return {"kind": "mailto", "to": to_addr, "subject": subject}


def extract_list_unsubscribe(raw_msg: Message | None) -> dict[str, str] | None:
    """Parse ``List-Unsubscribe`` into the message-detail ``unsubscribe`` field.

    Returns ``None`` when the header is absent or unusable, otherwise one of:
      ``{"kind": "url", "url": ...}`` for the first ``<http(s)://...>`` entry, or
      ``{"kind": "mailto", "to": ..., "subject": ...}`` when only usable
      ``<mailto:...>`` entries exist.

    http(s) wins over mailto: it opens in a new tab and sends nothing on the
    user's behalf (U8.10 is purely client-driven).
    """
    if raw_msg is None:
        return None
    header = _unfold_header(raw_msg.get("List-Unsubscribe"))
    if not header:
        return None
    mailto: dict[str, str] | None = None
    for raw_segment in _HEADER_SEGMENT_RE.findall(header):
        # Folded headers leave whitespace inside the URI token; literal
        # whitespace in a URI must be percent-encoded, so stripping it
        # restores the token the sender intended.
        segment = raw_segment.replace(" ", "")
        if not segment:
            continue
        lower = segment.lower()
        if lower.startswith(("http://", "https://")):
            if len(segment) <= _MAX_URL_CHARS and '"' not in segment and "'" not in segment:
                return {"kind": "url", "url": segment}
            continue
        if lower.startswith("mailto:") and mailto is None:
            mailto = _parse_mailto(segment)
    return mailto
