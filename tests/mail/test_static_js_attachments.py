"""Static guards for the attachment interaction JS (U8.8c/U8.8d).

The message preview pane is injected via innerHTML, which never executes
inline <script> tags — wiring attachments with per-element listeners at load
time is a dead-button bug. These tests keep the delegation pattern (and the
gesture-safe popup flow) from regressing.
"""

from pathlib import Path

MAIL_TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "modules" / "mail" / "templates"
ATTACHMENTS_JS = (
    Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "mail" / "attachments.js"
)


def test_attachments_js_uses_document_delegation():
    source = ATTACHMENTS_JS.read_text()
    assert 'document.addEventListener("click"' in source
    # Per-element wiring cannot survive innerHTML injection of the preview pane.
    assert 'querySelectorAll("[data-open-in-docs]")' not in source


def test_open_in_docs_opens_tab_synchronously_with_gesture():
    """U8.8d: window.open must happen before any awaited fetch resolves."""
    source = ATTACHMENTS_JS.read_text()
    fn_body = source[
        source.index("function openInDocs") : source.index(
            "// --------------------------------------------------------------- delegation"
        )
    ]
    open_index = fn_body.index('window.open("", "_blank")')
    first_then = fn_body.index(".then(")
    assert open_index < first_then, "window.open must be called synchronously in the click handler"


def test_pdf_preview_opens_top_level_tab_not_iframe():
    """iOS cannot render PDFs inside iframes — PDFs get a top-level tab."""
    source = ATTACHMENTS_JS.read_text()
    pdf_branch = source[source.index('kind === "pdf"') : source.index('kind === "other"')]
    assert 'window.open(viewUrl, "_blank")' in pdf_branch


def test_overlay_iframe_is_sandboxed():
    source = ATTACHMENTS_JS.read_text()
    assert 'frame.setAttribute("sandbox", "")' in source


def test_attachment_pages_include_attachments_js_with_cache_busting():
    message = (MAIL_TEMPLATES / "message.html").read_text()
    folder = (MAIL_TEMPLATES / "folder.html").read_text()
    expected = "js/mail/attachments.js"
    assert expected in message and "static_v(" in message
    assert expected in folder and "static_v(" in folder


def test_preview_partial_has_no_inline_script_wiring():
    """The preview partial is innerHTML-injected: any inline script is dead."""
    preview = (MAIL_TEMPLATES / "message_preview.html").read_text()
    assert "<script>" not in preview
    assert "querySelectorAll" not in preview
