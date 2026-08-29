"""Shared helpers for pre-staging MIME attachments onto the compose form and
building outgoing MIME messages with inline (multipart/related) image support.

Used by the forward prefill, draft resume, undo-send restore, and the
send / save-draft paths in the compose controller.
"""

import base64
import logging
import re
import uuid
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, NotRequired, TypedDict

from flask import current_app

from app.modules.mail.services import attachments as staging
from app.modules.mail.utils.sanitize import normalize_header_text

logger = logging.getLogger(__name__)

_DEFAULT_MAX_FILE = 25 * 1024 * 1024
_DEFAULT_MAX_TOTAL = 50 * 1024 * 1024

_IMAGE_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
}

_CID_IMG_RE = re.compile(r"<img\b[^>]*src=[\"']cid:[^\"']+[\"'][^>]*>", re.IGNORECASE)


class StagedFile(TypedDict):
    """A staged attachment collected for building an outgoing message."""

    name: str
    mime: str
    data: bytes
    content_id: NotRequired[str]


def _max_file_bytes() -> int:
    try:
        return int(current_app.config.get("MAIL_ATTACHMENT_MAX_FILE_BYTES", _DEFAULT_MAX_FILE))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_FILE


def _max_total_bytes() -> int:
    try:
        return int(current_app.config.get("MAIL_ATTACHMENT_MAX_TOTAL_BYTES", _DEFAULT_MAX_TOTAL))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_TOTAL


def is_attachment_part(part: Any) -> bool:
    """True when a MIME part should be treated as an attachment."""
    disposition = part.get_content_disposition()
    if disposition == "attachment":
        return True
    content_type = part.get_content_type()
    if content_type.startswith("multipart/"):
        return False
    if content_type in ("text/plain", "text/html"):
        return False
    return bool(part.get_filename())


def _fallback_name(part: Any, index: int) -> str:
    content_id = (part.get("Content-ID") or "").strip().strip("<>")
    if content_id:
        ext = _IMAGE_EXT_BY_MIME.get(part.get_content_type() or "")
        return f"{content_id}.{ext}" if ext else content_id
    content_type = part.get_content_type() or ""
    if content_type in _IMAGE_EXT_BY_MIME:
        return f"image{index}.{_IMAGE_EXT_BY_MIME[content_type]}"
    return f"attachment-{index}"


def _data_url(mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{encoded}"


def stage_mime_attachments(user_id: int | None, raw_msg: Any) -> dict[str, Any]:
    """Stage every attachment part of a MIME message into a fresh compose session.

    Returns a dict with:
      - compose_session_id: str | None (None when nothing was staged)
      - attachments: [{"id", "name", "size", "mime"}] seeds for the compose UI
      - cid_data_urls: {content_id: "data:<mime>;base64,..."} for editor display
      - notices: human-readable strings for skipped (over-limit) parts
    """
    result: dict[str, Any] = {
        "compose_session_id": None,
        "attachments": [],
        "cid_data_urls": {},
        "notices": [],
    }
    if raw_msg is None or user_id is None:
        return result
    parts = [part for part in raw_msg.walk() if is_attachment_part(part)]
    if not parts:
        return result
    session_id = uuid.uuid4().hex
    max_file = _max_file_bytes()
    max_total = _max_total_bytes()
    total = 0
    staged_any = False
    for index, part in enumerate(parts):
        try:
            data = part.get_payload(decode=True) or b""
            name = normalize_header_text(part.get_filename()) or _fallback_name(part, index)
            mime = part.get_content_type() or "application/octet-stream"
            if len(data) > max_file:
                result["notices"].append(
                    f'"{name}" exceeds the per-file attachment limit and was not attached.'
                )
                continue
            total += len(data)
            if total > max_total:
                result["notices"].append(
                    "Remaining attachments exceed the total size limit and were not attached."
                )
                break
            content_id = (part.get("Content-ID") or "").strip().strip("<>")
            file_id = uuid.uuid4().hex
            staging.stage_file(
                user_id, session_id, file_id, data, name, mime, content_id=content_id or None
            )
            staged_any = True
            result["attachments"].append(
                {"id": file_id, "name": name, "size": len(data), "mime": mime}
            )
            if content_id:
                result["cid_data_urls"][content_id] = _data_url(mime, data)
        except Exception:
            logger.warning("failed to stage MIME attachment index=%d", index, exc_info=True)
            result["notices"].append("One attachment could not be added automatically.")
    if staged_any:
        result["compose_session_id"] = session_id
    else:
        staging.delete_session(user_id, session_id)
    return result


def rewrite_cid_srcs(html: str, cid_data_urls: dict[str, str]) -> str:
    """Replace cid: image sources with data URLs so they render in the editor."""
    if not html or not cid_data_urls:
        return html or ""

    def _replace(match: re.Match[str]) -> str:
        cid = match.group(2)
        data_url = cid_data_urls.get(cid)
        if data_url:
            return f'src="{data_url}"'
        return match.group(0)

    pattern = re.compile(r'src=(["\'])cid:([^"\']+)\1', re.IGNORECASE)
    return pattern.sub(_replace, html)


def strip_cid_imgs(html: str) -> str:
    """Remove inline cid: images (used for reply quotes, which carry no parts)."""
    if not html:
        return html
    return _CID_IMG_RE.sub("", html)


def apply_inline_content_ids(body_html: str, staged_files: list[StagedFile]) -> str:
    """Convert staged inline-image data URLs back to cid: references for sending."""
    if not body_html:
        return body_html
    for file in staged_files:
        content_id = file.get("content_id")
        if not content_id:
            continue
        body_html = body_html.replace(
            _data_url(file.get("mime", ""), file["data"]), f"cid:{content_id}"
        )
    return body_html


def _mime_base(mime: str) -> MIMEBase:
    maintype, _, subtype = (mime or "application/octet-stream").partition("/")
    return MIMEBase(maintype or "application", subtype or "octet-stream")


def _inline_part(file: StagedFile) -> MIMEBase:
    part = _mime_base(file.get("mime", ""))
    part.set_payload(file["data"])
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "inline", filename=file["name"])
    part.add_header("Content-ID", f"<{file.get('content_id')}>")
    return part


def _attachment_part(file: StagedFile) -> MIMEBase:
    part = _mime_base(file.get("mime", ""))
    part.set_payload(file["data"])
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=file["name"])
    return part


def build_body_container(
    body: str, body_html: str, staged_files: list[StagedFile]
) -> MIMEMultipart:
    """Build the body container: multipart/related when inline images exist, else
    multipart/alternative."""
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body or "", "plain"))
    if body_html:
        alt.attach(MIMEText(body_html, "html"))
    inline_files = [f for f in staged_files if f.get("content_id")]
    if not inline_files:
        return alt
    related = MIMEMultipart("related")
    related.attach(alt)
    for file in inline_files:
        related.attach(_inline_part(file))
    return related


def build_message_root(
    headers: list[tuple[str, str]],
    body: str,
    body_html: str,
    staged_files: list[StagedFile],
) -> MIMEMultipart:
    """Build a multipart/mixed root: headers + body container + regular attachments."""
    root = MIMEMultipart("mixed")
    for name, value in headers:
        if value:
            root[name] = value
    root.attach(build_body_container(body, body_html, staged_files))
    for file in staged_files:
        if not file.get("content_id"):
            root.attach(_attachment_part(file))
    return root
