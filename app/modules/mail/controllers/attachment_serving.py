"""Helpers for serving and previewing message attachments (U8.8a-U8.8d).

Kept separate from ``message.py`` to keep controller files small: these are
the pieces shared by ``download_attachment`` and ``view_attachment``.
"""

from __future__ import annotations

import mimetypes
from typing import Any

from flask import render_template
from flask_babel import _


def normalized_content_type(filename: str, declared: str | None) -> str:
    """Content-Type for an attachment response (U8.8a).

    The filename extension wins over the sender-declared MIME type: mail
    clients routinely send PDFs and images as ``application/octet-stream``,
    which iOS browsers refuse to render inline regardless of disposition.
    The declared type is only a fallback for unknown extensions.
    """
    guessed, _encoding = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    declared = (declared or "").strip().lower()
    return declared or "application/octet-stream"


def attachment_actions_map(
    attachments: list[dict[str, Any]],
    extra_filenames: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Template map of filename -> actions, augmented with ``kind`` (U8.8c).

    ``extra_filenames`` covers attachments of collapsed thread messages, which
    are not part of the current message's attachment list but still render
    chips.
    """
    from app.shared.pandoc_formats import get_attachment_actions, preview_kind

    result: dict[str, dict[str, Any]] = {}
    for filename in [a["filename"] for a in attachments] + list(extra_filenames or []):
        if filename in result:
            continue
        actions = get_attachment_actions(filename)
        actions["kind"] = preview_kind(filename)
        result[filename] = actions
    return result


def attachment_unavailable() -> tuple[str, int]:
    """410 error page for attachments that can no longer be loaded (U8.8b).

    Never a redirect: a silent 302 back to the message view looks like a
    mystery page reload and loses the user's place in the app. Callers log
    the failure with message/account identifiers.
    """
    html = render_template(
        "attachment_error.html",
        title=_("Attachment unavailable"),
        message=_(
            "This attachment could not be loaded. It may have been moved or"
            " deleted, or the mail server could not be reached."
        ),
        filename=None,
        download_url=None,
    )
    return html, 410


def attachment_notice(
    message: str,
    filename: str | None,
    download_url: str | None,
) -> tuple[str, int]:
    """200 notice page: the file is available but cannot be previewed."""
    html = render_template(
        "attachment_error.html",
        title=_("No preview available"),
        message=message,
        filename=filename,
        download_url=download_url,
    )
    return html, 200
