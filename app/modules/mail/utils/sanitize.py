import html
import inspect
import json
import os
import re
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any

import bleach
import tinycss2
from bleach.css_sanitizer import CSSSanitizer

ALLOWED_TAGS = [
    "a",
    "article",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "section",
    "span",
    "strong",
    "style",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
]
ALLOWED_ATTRS = {
    # "hidden" (U8.2b): senders hide preheaders with <div hidden>; dropping the
    # attribute would un-hide the padding run.
    "*": ["class", "hidden", "style"],
    "div": ["data-quote-block", "data-quote-summary", "data-quote-header"],
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "table": ["width", "cellpadding", "cellspacing", "border", "bgcolor", "align"],
    "td": ["width", "bgcolor", "valign", "align"],
    "th": ["width", "bgcolor", "valign", "align"],
    "tr": ["bgcolor", "valign", "align"],
}
ALLOWED_CSS_PROPERTIES = [
    "background",
    "background-color",
    "border",
    "border-bottom",
    "border-collapse",
    "border-color",
    "border-left",
    "border-radius",
    "border-right",
    "border-spacing",
    "border-top",
    "border-width",
    "box-sizing",
    "clip",
    "clip-path",
    "color",
    "display",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "height",
    "left",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "margin-top",
    "max-height",
    "max-width",
    "min-height",
    "min-width",
    "object-fit",
    "object-position",
    "opacity",
    "overflow",
    "overflow-wrap",
    "overflow-x",
    "overflow-y",
    "padding",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "padding-top",
    "position",
    "right",
    "table-layout",
    "text-align",
    "text-decoration",
    "text-transform",
    "top",
    "vertical-align",
    "visibility",
    "white-space",
    "width",
    "word-break",
    "word-wrap",
    "z-index",
]
_css_sanitizer_kwargs: dict[str, Any] = {"allowed_css_properties": ALLOWED_CSS_PROPERTIES}
if "allowed_at_rules" in inspect.signature(CSSSanitizer).parameters:
    _css_sanitizer_kwargs["allowed_at_rules"] = ["media"]
CSS_SANITIZER = CSSSanitizer(**_css_sanitizer_kwargs)


def _allow_attribute(tag: str, name: str, value: Any) -> bool:
    """Attribute allowlist for bleach, plus the U8.2a malformed-href rule.

    bleach treats scheme-less URI values as http(s) relatives and keeps them,
    so a sender-corrupted href (markup inside the attribute value, e.g.
    ``href="<a class="`` from a double-rendered footer anchor) would survive
    as a clickable junk link. Dropping the attribute renders the anchor text
    as plain text instead.
    """
    if name not in ALLOWED_ATTRS.get(tag, ()) and name not in ALLOWED_ATTRS.get("*", ()):
        return False
    if name in ("href", "src"):
        val = value or ""
        return "<" not in val and ">" not in val
    return True


URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
GREETING_RE = re.compile(
    r"^(hi|hello|dear|hey|greetings)\b[\s,.:;-]*([A-Z][\w'\u2019.-]*\s*){0,3}[,!:;.-]*\s*",
    re.IGNORECASE,
)
# U+034F (combining grapheme joiner) and U+180E are the invisible padding
# characters bulk-appended to email preheaders.
INVISIBLE_RE = re.compile(
    r"[\u00ad\u034f\u180e\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]"
)
CSS_RULE_RE = re.compile(r"[.#]?[A-Za-z0-9_-]+\s*\{[^}]*\}")
SEPARATOR_RE = re.compile(r"([*-_]{3,}|[•·]{3,}|[=]{3,})")
JSON_LD_RE = re.compile(r"\{\s*\"@context\".*?\}\s*", re.DOTALL)
BOILERPLATE_PHRASES = (
    "view in browser",
    "unsubscribe",
    "manage preferences",
    "email preferences",
    "update preferences",
    "privacy policy",
    "terms of service",
    "forward to a friend",
    "sent to you by",
    "you are receiving",
    "why am i receiving",
    "add us to your address book",
    "this email was sent",
)
HTML_BLOCK_CLOSE_RE = re.compile(
    r"</\s*(p|div|li|h[1-6]|tr|table|section|article|blockquote|ul|ol)\s*>",
    re.IGNORECASE,
)
HTML_BLOCK_OPEN_RE = re.compile(
    r"<\s*(p|div|li|h[1-6]|tr|table|section|article|blockquote|ul|ol)[^>]*>",
    re.IGNORECASE,
)
HTML_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
DEFAULT_SNIPPET_PATTERNS = {
    "greeting_phrases": ["hi", "hello", "dear", "hey", "greetings"],
    "header_phrases": [
        "view in browser",
        "view online",
        "web version",
        "open in browser",
        "email preferences",
        "manage preferences",
        "update preferences",
    ],
    "boilerplate_phrases": [
        *list(BOILERPLATE_PHRASES),
        "privacy policy",
        "terms of service",
        "forward to a friend",
        "sent to you by",
        "you are receiving",
        "why am i receiving",
        "all rights reserved",
        "copyright",
    ],
    "signature_markers": [
        "--",
        "__",
        "sent from my iphone",
        "sent from my ipad",
        "sent from my android",
        "sent from my mobile",
    ],
}
QUOTED_REPLY_RE = re.compile(r"^on .+ wrote:\s*$", re.IGNORECASE)
FORWARD_REPLY_RE = re.compile(
    r"^(begin forwarded message|forwarded message|original message)\b", re.IGNORECASE
)
HEADER_LINE_RE = re.compile(r"^(from|to|cc|bcc|sent|subject|date):\s*", re.IGNORECASE)
_SNIPPET_PATTERNS = None


_CID_IMG_PLACEHOLDER_PREFIX = "__LR_CID_IMG_"
_CID_IMG_PLACEHOLDER_RE = re.compile(r"__LR_CID_IMG_(\d+)__")

_CSS_URL_TOKEN_RE = re.compile(r"url\s*\(", re.IGNORECASE)
_STYLE_TAG_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.DOTALL | re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r'style="([^"]*)"')


def _sanitize_css_declarations(css_source, allow_remote):
    """Filter a CSS declaration list to allowed properties.

    Drops declarations whose value carries a remote url(...) unless the user
    allowed remote content, and rejects any serialized value containing "<" or
    ">" so sanitized CSS cannot break out of a <style> block or attribute.
    """
    kept = []
    for node in tinycss2.parse_declaration_list(css_source or []):
        if node.type != "declaration":
            continue
        name = node.lower_name
        if name not in ALLOWED_CSS_PROPERTIES:
            continue
        value = tinycss2.serialize(node.value).strip()
        if not value or "<" in value or ">" in value:
            continue
        if not allow_remote and _CSS_URL_TOKEN_RE.search(value):
            continue
        important = " !important" if node.important else ""
        kept.append(f"{name}:{value}{important};")
    return "".join(kept)


def _sanitize_stylesheet(css_text, allow_remote):
    """Sanitize a full stylesheet (sender <style> block contents).

    Keeps qualified rules and @media blocks (recursively sanitized so
    responsive emails keep adapting); drops every other at-rule
    (@import, @font-face, ...). Remote url() values follow allow_remote.
    """

    def walk(rules):
        out = []
        for rule in rules:
            if rule.type == "at-rule" and rule.lower_at_keyword == "media":
                if rule.content is None:
                    continue
                inner = walk(tinycss2.parse_rule_list(rule.content))
                if not inner:
                    continue
                prelude = tinycss2.serialize(rule.prelude).strip()
                if not prelude or "<" in prelude or ">" in prelude:
                    continue
                out.append("@media " + prelude + "{" + inner + "}")
            elif rule.type == "qualified-rule":
                selector = tinycss2.serialize(rule.prelude).strip()
                if not selector or "<" in selector or ">" in selector:
                    continue
                declarations = _sanitize_css_declarations(rule.content, allow_remote)
                if declarations:
                    out.append(selector + "{" + declarations + "}")
        return "".join(out)

    return walk(tinycss2.parse_rule_list(css_text or ""))


def _sanitize_style_tags(fragment, allow_remote):
    def _repl(match):
        cleaned = _sanitize_stylesheet(match.group(1), allow_remote)
        return f"<style>{cleaned}</style>" if cleaned else ""

    return _STYLE_TAG_RE.sub(_repl, fragment)


def _strip_inline_style_urls(fragment):
    """Drop url()-bearing declarations from inline style attributes.

    bleach's CSSSanitizer filters properties but lets url(...) values through,
    which is a remote-fetch (tracking) vector while images are blocked.
    Attributes without url( pass through untouched.
    """
    if not _CSS_URL_TOKEN_RE.search(fragment):
        return fragment

    def _repl(match):
        css = match.group(1)
        if not _CSS_URL_TOKEN_RE.search(css):
            return match.group(0)
        cleaned = _sanitize_css_declarations(css, allow_remote=False)
        if not cleaned:
            return ""
        return 'style="' + html.escape(cleaned, quote=True) + '"'

    return _STYLE_ATTR_RE.sub(_repl, fragment)


_ANCHOR_OPEN_RE = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
_TAG_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")


def _harden_anchors(fragment):
    """Force ``rel="noopener noreferrer"`` and drop sender ``target`` (U8.11).

    Body links open via the wrapper's ``<base target="_blank">``; sender
    ``target`` values are stripped (named targets are a Firefox COOP failure
    mode, ``_self`` would navigate the body iframe itself) and ``noopener
    noreferrer`` is merged into every ``rel`` (deduped, sender tokens kept)
    so opened tabs never keep an opener relationship or leak the referrer.
    Anchors without ``href`` are left untouched.
    """

    def _harden(m):
        tag = m.group(0)
        if not any(am.group(1).lower() == "href" for am in _TAG_ATTR_RE.finditer(tag)):
            return tag
        rel_value = ""
        for am in _TAG_ATTR_RE.finditer(tag):
            if am.group(1).lower() == "rel":
                raw = am.group(2)
                rel_value = raw[1:-1] if raw[:1] in ('"', "'") else raw
                break

        def _strip_attr(am):
            return " " if am.group(1).lower() in ("rel", "target") else am.group(0)

        stripped = _TAG_ATTR_RE.sub(_strip_attr, tag)
        tokens = [t for t in rel_value.split() if t]
        merged = " ".join(dict.fromkeys([*tokens, "noopener", "noreferrer"]))
        return stripped[:-1].rstrip() + f' rel="{merged}">'

    return _ANCHOR_OPEN_RE.sub(_harden, fragment)


def sanitize_html(html, allow_images=False):
    html = html or ""
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    cid_placeholders = []
    if not allow_images:

        def _preserve_or_strip_img(m):
            tag = m.group(0)
            src_match = re.search(r'\bsrc\s*=\s*["\']?([^"\'\s>]+)', tag, re.IGNORECASE)
            if src_match and src_match.group(1).lower().startswith("cid:"):
                idx = len(cid_placeholders)
                cid_placeholders.append(tag)
                return f"{_CID_IMG_PLACEHOLDER_PREFIX}{idx}__"
            alt_match = re.search(r'\balt\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
            if alt_match:
                return alt_match.group(1)
            alt_match = re.search(r"\balt\s*=\s*([^\s>]+)", tag, re.IGNORECASE)
            if alt_match:
                return alt_match.group(1)
            return ""

        html = re.sub(r"<img\b[^>]*/?>", _preserve_or_strip_img, html, flags=re.IGNORECASE)
    tags = list(ALLOWED_TAGS)
    if not allow_images and "img" in tags:
        tags.remove("img")
    cleaner = bleach.Cleaner(
        tags=tags,
        attributes=_allow_attribute,
        css_sanitizer=CSS_SANITIZER,
        protocols=["http", "https", "mailto", "tel", "cid"],
        strip=True,
        strip_comments=True,
    )
    html = cleaner.clean(html)
    html = _harden_anchors(html)
    html = _sanitize_style_tags(html, allow_remote=allow_images)
    if not allow_images:
        html = _strip_inline_style_urls(html)
    if cid_placeholders:

        def _restore_cid_placeholder(m):
            idx = int(m.group(1))
            if idx < len(cid_placeholders):
                return cid_placeholders[idx]
            return m.group(0)

        html = _CID_IMG_PLACEHOLDER_RE.sub(_restore_cid_placeholder, html)
    return html


def clean_plain_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    text = html.unescape(value or "")
    text = INVISIBLE_RE.sub("", text)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = JSON_LD_RE.sub(" ", text)
    text = CSS_RULE_RE.sub(" ", text)
    text = re.sub(r"@media[^{]*\{.*?\}", " ", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned_lines = []
    previous_blank = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if not previous_blank:
                cleaned_lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        if "{" in line or "}" in line:
            continue
        if line.lower().startswith(("@media", "@font-face")):
            continue
        if re.fullmatch(r"(?:[A-Za-z-]+\s*:\s*[^;]+;\s*){2,}", line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def html_to_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    cleaned = bleach.clean(value or "", tags=[], attributes={}, strip=True)
    cleaned = html.unescape(cleaned)
    cleaned = INVISIBLE_RE.sub("", cleaned)
    cleaned = CSS_RULE_RE.sub(" ", cleaned)
    cleaned = JSON_LD_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def html_to_text_lines(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    text = value or ""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = HTML_BR_RE.sub("\n", text)
    text = HTML_BLOCK_OPEN_RE.sub("\n", text)
    text = HTML_BLOCK_CLOSE_RE.sub("\n", text)
    cleaned = bleach.clean(text, tags=[], attributes={}, strip=True)
    cleaned = html.unescape(cleaned)
    cleaned = INVISIBLE_RE.sub("", cleaned)
    cleaned = CSS_RULE_RE.sub(" ", cleaned)
    cleaned = JSON_LD_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _normalize_for_compare(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip().lower()


def strip_subject_from_text(text, subject):
    subject_norm = _normalize_for_compare(subject)
    if not subject_norm:
        return text
    lines = [line.strip() for line in (text or "").splitlines()]
    if lines and _normalize_for_compare(lines[0]) == subject_norm:
        return "\n".join(lines[1:]).lstrip()
    return text


def strip_subject_from_html(body_html, subject):
    subject_norm = _normalize_for_compare(subject)
    if not subject_norm:
        return body_html
    tag_pattern = r"^\s*<(h1|h2|h3|h4|p|div|span)[^>]*>\s*{subject}\s*</\\1>"
    tag_pattern = tag_pattern.format(subject=re.escape(subject.strip()))
    stripped = re.sub(tag_pattern, "", body_html or "", flags=re.IGNORECASE)
    if stripped != (body_html or ""):
        return stripped.lstrip()
    text_only = re.sub(r"<[^>]+>", " ", body_html or "")
    text_only = _normalize_for_compare(text_only)
    if text_only.startswith(subject_norm):
        return re.sub(
            re.escape(subject), "", body_html or "", count=1, flags=re.IGNORECASE
        ).lstrip()
    return body_html


def plain_text_to_html(value, cleaned=False):
    cleaned_text = value if cleaned else clean_plain_text(value)
    escaped = html.escape(cleaned_text or "")
    return "<div>{}</div>".format(escaped.replace("\n", "<br>"))


def add_quoted_collapse(body_html):
    if not body_html:
        return body_html

    dq_start = re.search(r"<div\s+data-quote-block[\s=>]", body_html, re.IGNORECASE)
    if dq_start:
        before = body_html[: dq_start.start()]
        quoted = body_html[dq_start.start() :]
        before = re.sub(r"(<br\s*/?>\s*)+$", "", before)
        return (
            before + '<details class="lr-quoted">'
            '<summary class="lr-quoted-toggle">Show trimmed content</summary>'
            + quoted
            + "</details>"
        )

    has_blockquotes = bool(re.search(r"<blockquote[\s>]", body_html, re.IGNORECASE))

    if has_blockquotes:

        def _wrap_blockquote(m):
            attrs = m.group(1) or ""
            content = m.group(2)
            return (
                '<details class="lr-quoted">'
                '<summary class="lr-quoted-toggle">Show trimmed content</summary>'
                "<blockquote" + attrs + ">" + content + "</blockquote>"
                "</details>"
            )

        body_html = re.sub(
            r"<blockquote(\s[^>]*)?>(.*?)</blockquote\s*>",
            _wrap_blockquote,
            body_html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    else:
        gt_pattern = r"((?:&gt;[^\n<]*(?:<br\s*/?>|\n)){2,})"
        if re.search(gt_pattern, body_html, re.IGNORECASE):

            def _wrap_plain_quotes(m):
                return (
                    '<details class="lr-quoted">'
                    '<summary class="lr-quoted-toggle">Show trimmed content</summary>'
                    '<div class="lr-quoted-content">' + m.group(0) + "</div></details>"
                )

            body_html = re.sub(
                gt_pattern,
                _wrap_plain_quotes,
                body_html,
                flags=re.IGNORECASE,
            )
        else:
            on_wrote_re = re.compile(
                r"(?:On\s[^<]{10,}?\bwrote:|-{3,}Original\s+Message\s*-{3,})",
                re.IGNORECASE,
            )
            m = on_wrote_re.search(body_html)
            if m:
                before = body_html[: m.start()]
                quoted = body_html[m.start() :]
                before = re.sub(r"(<br\s*/?>\s*)+$", "", before)
                trailing = ""
                stripped_q = quoted.rstrip()
                if stripped_q.endswith("</div>"):
                    trailing = "</div>"
                    quoted = stripped_q[:-6]
                body_html = (
                    before + '<details class="lr-quoted">'
                    '<summary class="lr-quoted-toggle">Show trimmed content</summary>'
                    + quoted
                    + "</details>"
                    + trailing
                )

    return body_html


def wrap_email_html(body_html, text_scale: float = 1.0):
    """Wrap sanitized HTML in the iframe document (UX8/UX8c).

    ``text_scale`` multiplies the iframe's root font-size with the user's
    text-size setting; the default (1.0) renders exactly as before.
    """
    scale_css = f"calc(100% * {text_scale:g})"
    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8" />'
        '<base target="_blank" />'
        "<style>"
        f"html{{font-size:{scale_css};}}"
        "html,body{margin:0;padding:0;}"
        "body{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "font-size:0.9375rem;line-height:1.6;color:#0f172a;}"
        "img{max-width:100%;height:auto;}"
        "table{max-width:100%;}"
        "td,th{overflow-wrap:anywhere;}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;}"
        ".email-body{max-width:720px;margin:0 auto;padding:16px;overflow-wrap:break-word;}"
        ".lr-quoted{margin:8px 0;border-left:3px solid #e2e8f0;padding-left:0;}"
        ".lr-quoted-toggle{cursor:pointer;font-size:0.75rem;color:#94a3b8;"
        "padding:4px 8px;user-select:none;list-style:none;display:inline-block;}"
        ".lr-quoted-toggle:hover{color:#64748b;}"
        ".lr-quoted-toggle::-webkit-details-marker{display:none;}"
        ".lr-quoted-content,.lr-quoted blockquote{color:#64748b;font-size:0.875rem;line-height:1.5;}"
        ".lr-quoted[open] .lr-quoted-toggle{color:#64748b;}"
        "</style></head><body>"
        '<div class="email-body">'
        + (body_html or "")
        + "</div>"
        + "<script>"
        + "var _lrFitMode=1;"
        + "function _lrFitWrap(){return document.querySelector('.email-body');}"
        + "function _lrScaled(wrap){return !!wrap&&!!wrap.style.transform;}"
        + "function lrResize(){"
        + "try{"
        + "var wrap=_lrFitWrap();"
        + "var h=_lrScaled(wrap)?Math.ceil(wrap.getBoundingClientRect().height):document.body.scrollHeight;"
        + "parent.postMessage({lrIframeHeight:h},'*');"
        + "}catch(e){}}"
        + "function _lrMeasure(wrap){"
        + "return Math.max(document.documentElement.scrollWidth,"
        + "document.body.scrollWidth,wrap?wrap.scrollWidth:0);}"
        + "function lrFit(){"
        + "try{"
        + "var wrap=_lrFitWrap();"
        + "if(!wrap){parent.postMessage({lrFitAvailable:false,lrFitApplied:false},'*');}"
        + "else{"
        + "wrap.style.transform='';"
        + "var vw=window.innerWidth;"
        + "var sw=_lrMeasure(wrap);"
        + "var available=sw>vw+8;"
        + "if(available&&_lrFitMode===1){"
        + "wrap.style.transformOrigin='top left';"
        + "var s=(vw-1)/sw;"
        + "wrap.style.transform='scale('+s+')';"
        + "var sw2=Math.max(document.documentElement.scrollWidth,document.body.scrollWidth);"
        + "if(sw2>vw){wrap.style.transform='scale('+((vw-1)*s/sw2)+')';}"
        + "}"
        + "parent.postMessage({lrFitAvailable:available,"
        + "lrFitApplied:available&&_lrFitMode===1},'*');"
        + "}"
        + "}catch(e){}"
        + "lrResize();}"
        + "window.addEventListener('message',function(e){"
        + "if(e.source===parent&&e.data&&typeof e.data.lrFitSet==='number'){"
        + "_lrFitMode=e.data.lrFitSet;lrFit();}});"
        + "window.addEventListener('resize',lrFit);"
        + "document.addEventListener('load',lrFit,true);"
        + "lrFit();"
        + "window.addEventListener('load',lrFit);"
        + "new MutationObserver(lrFit).observe(document.body,{childList:true,subtree:true});"
        + "document.addEventListener('toggle',function(e){"
        + "if(e.target&&e.target.tagName==='DETAILS'){setTimeout(lrResize,0);}},true);"
        + "</script>"
        + "</body></html>"
    )


def decode_mime_header(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def normalize_header_text(value):
    decoded = decode_mime_header(value)
    cleaned = bleach.clean(decoded or "", tags=[], attributes={}, strip=True)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def decode_address_header(value):
    if not value:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    addresses = getaddresses([value])
    if not addresses:
        return normalize_header_text(value)
    formatted = []
    for name, addr in addresses:
        decoded_name = normalize_header_text(name)
        if addr:
            if decoded_name:
                formatted.append(f"{decoded_name} <{addr}>")
            else:
                formatted.append(addr)
        elif decoded_name:
            formatted.append(decoded_name)
    return ", ".join(formatted) if formatted else normalize_header_text(value)


def _load_snippet_patterns():
    global _SNIPPET_PATTERNS
    if _SNIPPET_PATTERNS is not None:
        return _SNIPPET_PATTERNS
    patterns: dict[str, Any] = {
        key: list(values) for key, values in DEFAULT_SNIPPET_PATTERNS.items()
    }
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.path.join(base_dir, "data", "snippet_patterns.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            for key, values in data.items():
                if not isinstance(values, list):
                    continue
                existing = patterns.setdefault(key, [])
                for value in values:
                    if isinstance(value, str) and value:
                        existing.append(value)
    except (OSError, json.JSONDecodeError):
        pass
    patterns["greeting_re"] = _build_greeting_re(patterns.get("greeting_phrases", []))
    patterns["boilerplate_phrases"] = [
        phrase.lower() for phrase in patterns.get("boilerplate_phrases", [])
    ]
    patterns["header_phrases"] = [phrase.lower() for phrase in patterns.get("header_phrases", [])]
    patterns["signature_markers"] = [
        phrase.lower() for phrase in patterns.get("signature_markers", [])
    ]
    _SNIPPET_PATTERNS = patterns
    return _SNIPPET_PATTERNS


def _build_greeting_re(phrases):
    if not phrases:
        return GREETING_RE
    escaped = "|".join(re.escape(phrase) for phrase in phrases if phrase)
    if not escaped:
        return GREETING_RE
    return re.compile(
        r"^(?:" + escaped + r")\b[\s,.:;-]*([A-Z][\w'.-]*\s*){0,3}[,!:;.-]*\s*",
        re.IGNORECASE,
    )


def _prepare_preview_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    text = html.unescape(value or "")
    text = INVISIBLE_RE.sub("", text)
    text = CSS_RULE_RE.sub(" ", text)
    text = JSON_LD_RE.sub(" ", text)
    return re.sub(r"\r\n?", "\n", text).strip()


def _split_paragraphs(text):
    return [chunk.strip() for chunk in re.split(r"\n\s*\n+", text) if chunk.strip()]


def _clean_preview_line(line):
    line = line.strip()
    if not line:
        return ""
    if (
        "." in line
        and "," in line
        and "{" not in line
        and "}" not in line
        and re.fullmatch(r"[.#A-Za-z0-9_,\s-]{10,}", line)
    ):
        return ""
    if "{" in line or "}" in line:
        return ""
    if re.fullmatch(r"(?:[A-Za-z-]+\s*:\s*[^;]+;\s*){2,}", line):
        return ""
    line = URL_RE.sub("", line).strip()
    line = SEPARATOR_RE.sub(" ", line).strip()
    line = re.sub(r"\s+", " ", line).strip()
    if not line:
        return ""
    return line


def _is_header_or_greeting(line, patterns):
    if patterns["greeting_re"].match(line):
        return True
    lower = line.lower()
    if HEADER_LINE_RE.match(line):
        return True
    if any(phrase in lower for phrase in patterns["header_phrases"]):
        return True
    if "|" in line:
        words = re.findall(r"[A-Za-z0-9]+", line)
        if 0 < len(words) <= 12 and not re.search(r"[.!?]$", line):
            return True
    if "•" in line and " by " in lower:
        return True
    if lower.startswith("by ") and "•" in line:
        return True
    if lower.startswith("in partnership with"):
        return True
    return bool(re.fullmatch(r"[A-Z0-9\s]{6,}", line))


def _is_boilerplate(line, patterns):
    lower = line.lower()
    if any(phrase in lower for phrase in patterns["boilerplate_phrases"]):
        return True
    return bool(any(marker in lower for marker in patterns["signature_markers"]))


def _is_quoted(line):
    if line.startswith(">"):
        return True
    lower = line.lower().strip()
    if QUOTED_REPLY_RE.match(line):
        return True
    return bool(FORWARD_REPLY_RE.match(lower))


def _is_meaningful(text, min_words=4, min_chars=20):
    words = re.findall(r"[A-Za-z0-9]+", text)
    if len(words) >= min_words and len(text) >= min_chars:
        return True
    if len(words) >= max(3, min_words):
        return len(text) >= min_chars
    return False


def _is_short_snippet(text, min_words=6, min_chars=40):
    words = re.findall(r"[A-Za-z0-9]+", text)
    return len(words) < min_words or len(text) < min_chars


def _trim_snippet_tail(text):
    if not text:
        return text
    text = re.sub(r"\s+[&•|/]\s*$", "", text).strip()
    text = re.sub(r"\s*[(&\[]\s*$", "", text).strip()
    text = re.sub(r"\s*(?:and|or|with|at)\s*$", "", text, flags=re.IGNORECASE).strip()
    return text.strip()


def _extract_paragraph_candidate(paragraph, patterns):
    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    if not lines:
        return ""
    cleaned = []
    skipping_leading = True
    for line in lines:
        line = _clean_preview_line(line)
        if not line:
            continue
        if _is_quoted(line):
            return ""
        if skipping_leading and _is_header_or_greeting(line, patterns):
            continue
        skipping_leading = False
        if _is_boilerplate(line, patterns):
            continue
        cleaned.append(line)
    if not cleaned:
        return ""
    candidate = " ".join(cleaned)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = re.sub(r"^[\W_]+", "", candidate).strip()
    return candidate


def _extract_paragraph_candidate_debug(paragraph, patterns):
    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    stats = {
        "line_total": len(lines),
        "line_skipped_header": 0,
        "line_skipped_boilerplate": 0,
        "line_skipped_quoted": 0,
        "line_skipped_empty": 0,
        "line_kept": 0,
    }
    if not lines:
        return "", stats, "empty"
    cleaned = []
    skipping_leading = True
    for line in lines:
        line = _clean_preview_line(line)
        if not line:
            stats["line_skipped_empty"] += 1
            continue
        if _is_quoted(line):
            stats["line_skipped_quoted"] += 1
            return "", stats, "quoted"
        if skipping_leading and _is_header_or_greeting(line, patterns):
            stats["line_skipped_header"] += 1
            continue
        skipping_leading = False
        if _is_boilerplate(line, patterns):
            stats["line_skipped_boilerplate"] += 1
            continue
        stats["line_kept"] += 1
        cleaned.append(line)
    if not cleaned:
        return "", stats, "filtered"
    candidate = " ".join(cleaned)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = re.sub(r"^[\W_]+", "", candidate).strip()
    return candidate, stats, "candidate"


def _normalize_preview_candidate_debug(value):
    text = _prepare_preview_text(value)
    info = {
        "paragraphs_total": 0,
        "paragraphs_scanned": 0,
        "candidates_considered": 0,
        "paragraphs": [],
        "selected_paragraph": None,
        "rejected_not_meaningful": 0,
        "selected_short_fallback": False,
    }
    if not text:
        return "", info
    patterns = _load_snippet_patterns()
    paragraphs = _split_paragraphs(text)
    info["paragraphs_total"] = len(paragraphs)
    short_fallback = None
    for idx, paragraph in enumerate(paragraphs):
        candidate, stats, status = _extract_paragraph_candidate_debug(paragraph, patterns)
        info["paragraphs_scanned"] += 1
        entry = {"index": idx, "status": status, "stats": stats}
        if not candidate:
            info["paragraphs"].append(entry)
            continue
        info["candidates_considered"] += 1
        words = len(re.findall(r"[A-Za-z0-9]+", candidate))
        chars = len(candidate)
        entry["words"] = words
        entry["chars"] = chars
        if _is_meaningful(candidate, min_words=4, min_chars=20):
            candidate = _trim_snippet_tail(candidate)
            words = len(re.findall(r"[A-Za-z0-9]+", candidate))
            chars = len(candidate)
            entry["words"] = words
            entry["chars"] = chars
            if not _is_short_snippet(candidate):
                entry["status"] = "selected"
                info["selected_paragraph"] = entry
                return candidate, info
            if short_fallback is None:
                entry["status"] = "short_fallback"
                info["selected_paragraph"] = entry
                short_fallback = candidate
                info["selected_short_fallback"] = True
            else:
                entry["status"] = "short_fallback_ignored"
            info["paragraphs"].append(entry)
            continue
        entry["status"] = "rejected_not_meaningful"
        info["rejected_not_meaningful"] += 1
        info["paragraphs"].append(entry)
    if short_fallback is not None:
        return short_fallback, info
    for idx, paragraph in enumerate(paragraphs):
        candidate, stats, status = _extract_paragraph_candidate_debug(paragraph, patterns)
        info["paragraphs_scanned"] += 1
        if candidate:
            words = len(re.findall(r"[A-Za-z0-9]+", candidate))
            chars = len(candidate)
            info["selected_paragraph"] = {
                "index": idx,
                "status": "selected_fallback",
                "stats": stats,
                "words": words,
                "chars": chars,
            }
            return candidate, info
    return "", info


def _normalize_preview_candidate(value):
    text = _prepare_preview_text(value)
    if not text:
        return ""
    patterns = _load_snippet_patterns()
    paragraphs = _split_paragraphs(text)
    short_fallback = None
    for paragraph in paragraphs:
        candidate = _extract_paragraph_candidate(paragraph, patterns)
        if candidate and _is_meaningful(candidate, min_words=4, min_chars=20):
            candidate = _trim_snippet_tail(candidate)
            if not _is_short_snippet(candidate):
                return candidate
            if short_fallback is None:
                short_fallback = candidate
    for paragraph in paragraphs:
        candidate = _extract_paragraph_candidate(paragraph, patterns)
        if candidate:
            return _trim_snippet_tail(candidate)
    if short_fallback is not None:
        return _trim_snippet_tail(short_fallback)
    return ""


def normalize_preview_text(value, limit=200, fallback=None):
    cleaned = _normalize_preview_candidate(value)
    if not cleaned and fallback is not None:
        cleaned = _normalize_preview_candidate(fallback)
    if limit and len(cleaned) > limit:
        return cleaned[:limit].rstrip()
    return cleaned


def normalize_preview_text_debug(value, limit=200, fallback=None):
    cleaned, info = _normalize_preview_candidate_debug(value)
    info["fallback_used"] = False
    if not cleaned and fallback is not None:
        fallback_text, fallback_info = _normalize_preview_candidate_debug(fallback)
        info["fallback_used"] = True
        info["fallback"] = fallback_info
        cleaned = fallback_text
    if limit and len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    info["snippet_length"] = len(cleaned)
    return cleaned, info


def build_snippet(text_plain, text_html, limit=500):
    snippet, _info = build_snippet_debug(text_plain, text_html, limit=limit)
    return snippet


def build_snippet_debug(text_plain, text_html, limit=500):
    preview_plain, plain_info = normalize_preview_text_debug(text_plain or "", limit=limit)
    if preview_plain:
        return preview_plain, {"source": "plain", "plain": plain_info, "html_used": False}
    if not text_html:
        return "", {"source": "plain", "plain": plain_info, "html_used": False}
    html_text = html_to_text_lines(text_html)
    preview_html, html_info = normalize_preview_text_debug(html_text, limit=limit)
    return preview_html, {
        "source": "html",
        "plain": plain_info,
        "html": html_info,
        "html_used": True,
    }
