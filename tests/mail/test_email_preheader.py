"""U8.2b: preheader/hidden-content preservation in the email sanitizer.

Marketing emails open the body with a hidden "preheader" div: the inbox
preview text followed by a long run of invisible padding characters
(U+034F + spaces). Senders hide it with geometric CSS such as
``height:0;max-width:0;overflow:hidden``. The sanitizer used to drop
``overflow`` while keeping ``max-width:0``, producing a visible
one-character-per-line column spanning the entire message (Seek job-alert
emails). These tests pin every hiding pattern so the CSS allowlist never
silently un-hides sender content again.
"""

from app.modules.mail.utils.sanitize import (
    build_snippet,
    html_to_text,
    sanitize_html,
    wrap_email_html,
)

PREHEADER_TEXT = (
    "Qantas Software Technical Lead: Founded in the Queensland outback in 1920, Qantas…"
)
PADDING = "͏ " * 12


class TestPreheaderHiding:
    def test_geometric_preheader_overflow_preserved(self):
        """The reported bug: height/max-width kept but overflow stripped
        renders a 0-width visible column. overflow:hidden must survive."""
        html = (
            '<div style="height:0;max-height:0;max-width:0;overflow:hidden;'
            f'mso-hide:all">{PREHEADER_TEXT}{PADDING}</div><p>Body text</p>'
        )
        result = sanitize_html(html, allow_images=True)
        assert "overflow:hidden" in result
        assert "max-width:0" in result
        assert "Body text" in result

    def test_offscreen_preheader_position_preserved(self):
        html = f'<div style="position:absolute;left:-9999px;top:0">{PREHEADER_TEXT}{PADDING}</div><p>Body</p>'
        result = sanitize_html(html, allow_images=True)
        assert "position:absolute" in result
        assert "left:-9999px" in result

    def test_visibility_and_opacity_preserved_inline(self):
        html = f'<div style="visibility:hidden;opacity:0;max-height:0">{PREHEADER_TEXT}</div><p>Body</p>'
        result = sanitize_html(html, allow_images=True)
        assert "visibility:hidden" in result
        assert "opacity:0" in result

    def test_clip_pattern_preserved(self):
        """Screen-reader-only preheader pattern (clip/clip-path)."""
        html = (
            '<div style="position:absolute;width:1px;height:1px;clip:rect(0 0 0 0);'
            f'clip-path:inset(50%)">{PREHEADER_TEXT}</div><p>Body</p>'
        )
        result = sanitize_html(html, allow_images=True)
        assert "clip:rect(0 0 0 0)" in result
        assert "clip-path:inset(50%)" in result

    def test_hidden_attribute_preserved(self):
        result = sanitize_html(f"<div hidden>{PREHEADER_TEXT}{PADDING}</div><p>Body</p>")
        assert "<div hidden" in result

    def test_class_based_hiding_preserved_in_style_tag(self):
        html = (
            "<style>.preheader{visibility:hidden;overflow:hidden;max-height:0}</style>"
            f'<div class="preheader">{PREHEADER_TEXT}</div><p>Body</p>'
        )
        result = sanitize_html(html, allow_images=False)
        assert ".preheader{visibility:hidden;overflow:hidden;max-height:0;}" in result

    def test_display_none_preheader_still_hidden(self):
        """Canonical (Mailchimp-style) preheader: was already hidden via
        display:none — guard against regressions."""
        html = (
            '<div style="display:none;font-size:1px;color:#eef2f5;line-height:1px;'
            f'max-height:0;max-width:0;opacity:0;overflow:hidden">{PREHEADER_TEXT}{PADDING}</div>'
            "<p>Body text</p>"
        )
        result = sanitize_html(html, allow_images=True)
        assert "display:none" in result

    def test_seek_style_email_full_pipeline(self):
        """End-to-end: sanitized + wrapped body keeps the hiding CSS inside
        the iframe document."""
        html = (
            f'<div style="height:0;max-height:0;max-width:0;overflow:hidden">{PREHEADER_TEXT}{PADDING}</div>'
            "<table><tr><td><h2>Qantas Software Technical Lead</h2><p>Job details here.</p></td></tr></table>"
        )
        wrapped = wrap_email_html(sanitize_html(html, allow_images=False))
        assert "overflow:hidden" in wrapped


class TestPreheaderSecurityGuards:
    """U8.1/UX8b remote-content and breakout rules must keep holding for
    the newly allowed properties."""

    def test_inline_url_still_dropped_when_images_blocked(self):
        html = '<div style="overflow:hidden;background:url(https://evil.example/x)">hi</div>'
        result = sanitize_html(html, allow_images=False)
        assert "overflow:hidden" in result
        assert "evil.example" not in result

    def test_style_tag_url_in_new_property_dropped_when_blocked(self):
        html = "<style>.a{left:0;background:url(https://evil.example/x)}</style><p>hi</p>"
        result = sanitize_html(html, allow_images=False)
        assert "left:0" in result
        assert "evil.example" not in result

    def test_breakout_via_new_property_rejected(self):
        html = "<style>.x{left:'</style><script>alert(1)</script>'}</style><p>hi</p>"
        result = sanitize_html(html, allow_images=False)
        assert "<script" not in result
        assert "alert" not in result


class TestPreheaderTextExtraction:
    def test_invisible_padding_stripped_from_html_to_text(self):
        text = html_to_text(f"<div>{PREHEADER_TEXT}{PADDING}</div>")
        assert "͏" not in text
        assert PREHEADER_TEXT in text

    def test_invisible_padding_stripped_from_snippet(self):
        snippet = build_snippet("", f"<div>{PREHEADER_TEXT}{PADDING}</div>")
        assert "͏" not in snippet
        assert snippet.startswith("Qantas Software Technical Lead")
