import io
import re
import zipfile
from html.parser import HTMLParser

_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_FO = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
_SVG = "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"

_VOID = frozenset(["br", "hr", "img", "input", "meta", "link"])
_BLOCK = frozenset(["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "table", "thead", "tbody", "tr", "div"])

_HEADING = {"h1": "Heading_20_1", "h2": "Heading_20_2", "h3": "Heading_20_3", "h4": "Heading_20_4"}

_LIST_RE = re.compile(r"(\S[^\n]*)\n(?=[-\*+] .+\n|\d+[.\)] .+\n)")
_BULLET_LINE = re.compile(r"^[-\*+] .+$", re.MULTILINE)
_NUM_LINE = re.compile(r"^\d+[.\)] .+$", re.MULTILINE)


def _separate_list_types(text):
    lines = text.split("\n")
    result = []
    for i, line in enumerate(lines):
        prev_is_bullet = i > 0 and _BULLET_LINE.match(lines[i - 1])
        prev_is_num = i > 0 and _NUM_LINE.match(lines[i - 1])
        cur_is_bullet = _BULLET_LINE.match(line)
        cur_is_num = _NUM_LINE.match(line)
        if (prev_is_bullet and cur_is_num) or (prev_is_num and cur_is_bullet):
            result.append("")
        result.append(line)
    return "\n".join(result)


def convert(markdown_text):
    normalized = _LIST_RE.sub(r"\1\n\n", markdown_text)
    normalized = _separate_list_types(normalized)
    html = _to_html(normalized)
    tree = _parse(html)
    body_xml = _render_children(tree)
    return _build_zip(body_xml)


def _to_html(text):
    import markdown
    return markdown.Markdown(extensions=["extra"]).convert(text)


def _esc(text):
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Node:
    __slots__ = ("tag", "attrs", "children", "text", "tail")

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.text = ""
        self.tail = ""


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = _Node("root")
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        cur = self._stack[-1]
        if cur.children:
            last = cur.children[-1]
            last.tail = (last.tail or "") + data
        else:
            cur.text = (cur.text or "") + data


def _parse(html_str):
    b = _TreeBuilder()
    b.feed(html_str)
    return b.root


def _render_children(node, ctx=None):
    if ctx is None:
        ctx = {}
    return "".join(_render(c, ctx) for c in node.children)


def _render(node, ctx):
    tag = node.tag

    if tag == "root":
        return _render_children(node, ctx)

    if tag in _HEADING:
        return f'<text:p text:style-name="{_HEADING[tag]}">{_inline(node)}</text:p>'

    if tag == "p":
        style = "Quotations" if ctx.get("bq") else "Text_20_body"
        return f'<text:p text:style-name="{style}">{_inline(node)}</text:p>'

    if tag in ("ul", "ol"):
        ls = "List_Bullet" if tag == "ul" else "List_Number"
        items = "".join(_render(c, ctx) for c in node.children)
        return f"<text:list text:style-name=\"{ls}\">{items}</text:list>"

    if tag == "li":
        return _render_li(node, ctx)

    if tag == "blockquote":
        return _render_children(node, {**ctx, "bq": True})

    if tag == "pre":
        return f'<text:p text:style-name="Preformatted_20_Text">{_inline(node)}</text:p>'

    if tag == "hr":
        return '<text:p text:style-name="Horizontal_20_Line"/>'

    if tag == "table":
        inner = "".join(_render(c, ctx) for c in node.children)
        return f"<table:table>{inner}</table:table>"

    if tag in ("thead", "tbody", "tfoot"):
        return "".join(_render(c, ctx) for c in node.children)

    if tag == "tr":
        inner = "".join(_render(c, ctx) for c in node.children)
        return f"<table:table-row>{inner}</table:table-row>"

    if tag in ("th", "td"):
        s = "Table_20_Heading" if tag == "th" else "Table_20_Contents"
        return f'<table:table-cell><text:p text:style-name="{s}">{_inline(node)}</text:p></table:table-cell>'

    if tag in _VOID:
        if tag == "br":
            return "<text:line-break/>"
        if tag == "hr":
            return '<text:p text:style-name="Horizontal_20_Line"/>'
        return ""

    return _render_children(node, ctx)


def _render_li(node, ctx):
    parts = []
    inline_acc = []

    def flush_inline():
        if inline_acc:
            joined = "".join(inline_acc)
            if joined.strip():
                parts.append(f'<text:p text:style-name="ListParagraph">{joined}</text:p>')
            inline_acc.clear()

    if node.text and node.text.strip():
        inline_acc.append(_esc(node.text))

    for child in node.children:
        if child.tag in _BLOCK:
            flush_inline()
            parts.append(_render(child, ctx))
        else:
            inline_acc.append(_inline_el(child))
            if child.tail and child.tail.strip():
                inline_acc.append(_esc(child.tail))

    flush_inline()

    if not parts:
        parts.append('<text:p text:style-name="ListParagraph"/>')

    return f'<text:list-item>{"".join(parts)}</text:list-item>'


def _inline(node):
    parts = []
    if node.text:
        parts.append(_esc(node.text))
    for child in node.children:
        parts.append(_inline_el(child))
        if child.tail:
            parts.append(_esc(child.tail))
    return "".join(parts)


def _inline_el(node):
    tag = node.tag
    content = _inline(node)
    if tag in ("strong", "b"):
        return f'<text:span text:style-name="T_Bold">{content}</text:span>'
    if tag in ("em", "i"):
        return f'<text:span text:style-name="T_Italic">{content}</text:span>'
    if tag == "code":
        return f'<text:span text:style-name="T_Code">{content}</text:span>'
    if tag == "br":
        return "<text:line-break/>"
    if tag == "a":
        return content
    return content


def _styles_xml():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles
    xmlns:office="{_OFFICE}" xmlns:style="{_STYLE}" xmlns:fo="{_FO}"
    xmlns:text="{_TEXT}" xmlns:svg="{_SVG}" xmlns:table="{_TABLE}"
    office:version="1.2">
  <office:font-face-decls>
    <style:font-face style:name="Liberation Serif" svg:font-family="'Liberation Serif'"
        style:font-family-generic="roman" style:font-pitch="variable"/>
    <style:font-face style:name="Liberation Sans" svg:font-family="'Liberation Sans'"
        style:font-family-generic="swiss" style:font-pitch="variable"/>
    <style:font-face style:name="Liberation Mono" svg:font-family="'Liberation Mono'"
        style:font-family-generic="modern" style:font-pitch="fixed"/>
  </office:font-face-decls>
  <office:styles>
    <style:style style:name="Standard" style:family="paragraph">
      <style:text-properties style:font-name="Liberation Serif" fo:font-size="12pt"/>
    </style:style>
    <style:style style:name="Text_20_body" style:family="paragraph"
        style:parent-style-name="Standard" style:class="text">
      <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0.35cm"/>
    </style:style>
    <style:style style:name="ListParagraph" style:family="paragraph"
        style:parent-style-name="Standard" style:class="text">
      <style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0cm"/>
    </style:style>
    <style:style style:name="Heading" style:family="paragraph"
        style:parent-style-name="Standard" style:class="chapter">
      <style:paragraph-properties fo:margin-top="0.5cm" fo:margin-bottom="0.21cm"
          fo:keep-with-next="always"/>
      <style:text-properties style:font-name="Liberation Sans" fo:font-size="14pt"/>
    </style:style>
    <style:style style:name="Heading_20_1" style:family="paragraph"
        style:parent-style-name="Heading" style:next-style-name="Text_20_body" style:class="chapter">
      <style:paragraph-properties fo:margin-top="0.85cm" fo:margin-bottom="0.21cm"/>
      <style:text-properties fo:font-size="24pt" fo:font-weight="bold"/>
    </style:style>
    <style:style style:name="Heading_20_2" style:family="paragraph"
        style:parent-style-name="Heading" style:next-style-name="Text_20_body" style:class="chapter">
      <style:paragraph-properties fo:margin-top="0.75cm" fo:margin-bottom="0.21cm"/>
      <style:text-properties fo:font-size="18pt" fo:font-weight="bold"/>
    </style:style>
    <style:style style:name="Heading_20_3" style:family="paragraph"
        style:parent-style-name="Heading" style:next-style-name="Text_20_body" style:class="chapter">
      <style:paragraph-properties fo:margin-top="0.6cm" fo:margin-bottom="0.14cm"/>
      <style:text-properties fo:font-size="14pt" fo:font-weight="bold"/>
    </style:style>
    <style:style style:name="Heading_20_4" style:family="paragraph"
        style:parent-style-name="Heading" style:next-style-name="Text_20_body" style:class="chapter">
      <style:paragraph-properties fo:margin-top="0.5cm" fo:margin-bottom="0.14cm"/>
      <style:text-properties fo:font-size="12pt" fo:font-weight="bold"/>
    </style:style>
    <style:style style:name="Quotations" style:family="paragraph"
        style:parent-style-name="Standard" style:class="html">
      <style:paragraph-properties fo:margin-left="1.25cm" fo:margin-right="1.25cm"
          fo:margin-top="0.35cm" fo:margin-bottom="0.35cm"/>
      <style:text-properties fo:font-style="italic"/>
    </style:style>
    <style:style style:name="Preformatted_20_Text" style:family="paragraph"
        style:parent-style-name="Standard" style:class="html">
      <style:text-properties style:font-name="Liberation Mono" fo:font-size="10pt"/>
    </style:style>
    <style:style style:name="Horizontal_20_Line" style:family="paragraph"
        style:parent-style-name="Standard" style:class="html">
      <style:paragraph-properties fo:margin-top="0.5cm" fo:margin-bottom="0.5cm"
          fo:border="none" fo:border-bottom="0.5pt solid #cccccc"/>
    </style:style>
    <style:style style:name="Table_20_Heading" style:family="paragraph"
        style:parent-style-name="Standard" style:class="extra">
      <style:text-properties fo:font-weight="bold"/>
    </style:style>
    <style:style style:name="Table_20_Contents" style:family="paragraph"
        style:parent-style-name="Standard" style:class="extra"/>
    <style:style style:name="T_Bold" style:family="text">
      <style:text-properties fo:font-weight="bold"/>
    </style:style>
    <style:style style:name="T_Italic" style:family="text">
      <style:text-properties fo:font-style="italic"/>
    </style:style>
    <style:style style:name="T_Code" style:family="text">
      <style:text-properties style:font-name="Liberation Mono" fo:font-size="10pt"/>
    </style:style>
  </office:styles>
  <office:automatic-styles>
    <style:page-layout style:name="Mpm1">
      <style:page-layout-properties fo:page-width="21.0cm" fo:page-height="29.7cm"
          fo:margin-top="2.54cm" fo:margin-bottom="2.54cm"
          fo:margin-left="2.54cm" fo:margin-right="2.54cm"
          style:print-orientation="portrait"/>
    </style:page-layout>
  </office:automatic-styles>
  <office:master-styles>
    <style:master-page style:name="Standard" style:page-layout-name="Mpm1"/>
  </office:master-styles>
</office:document-styles>"""


def _content_xml(body):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
    xmlns:office="{_OFFICE}" xmlns:style="{_STYLE}" xmlns:fo="{_FO}"
    xmlns:text="{_TEXT}" xmlns:svg="{_SVG}" xmlns:table="{_TABLE}"
    office:version="1.2">
  <office:scripts/>
  <office:font-face-decls>
    <style:font-face style:name="Liberation Serif" svg:font-family="'Liberation Serif'"
        style:font-family-generic="roman" style:font-pitch="variable"/>
    <style:font-face style:name="Liberation Sans" svg:font-family="'Liberation Sans'"
        style:font-family-generic="swiss" style:font-pitch="variable"/>
    <style:font-face style:name="Liberation Mono" svg:font-family="'Liberation Mono'"
        style:font-family-generic="modern" style:font-pitch="fixed"/>
  </office:font-face-decls>
  <office:automatic-styles>
    <text:list-style style:name="List_Bullet">
      <text:list-level-style-bullet text:level="1" text:bullet-char="\u2022">
        <style:list-level-properties text:space-before="0.5cm" text:min-label-width="0.5cm"/>
      </text:list-level-style-bullet>
      <text:list-level-style-bullet text:level="2" text:bullet-char="\u25e6">
        <style:list-level-properties text:space-before="1.0cm" text:min-label-width="0.5cm"/>
      </text:list-level-style-bullet>
      <text:list-level-style-bullet text:level="3" text:bullet-char="\u25aa">
        <style:list-level-properties text:space-before="1.5cm" text:min-label-width="0.5cm"/>
      </text:list-level-style-bullet>
    </text:list-style>
    <text:list-style style:name="List_Number">
      <text:list-level-style-number text:level="1" style:num-format="1">
        <style:list-level-properties text:space-before="0.5cm" text:min-label-width="0.5cm"/>
      </text:list-level-style-number>
      <text:list-level-style-number text:level="2" style:num-format="a">
        <style:list-level-properties text:space-before="1.0cm" text:min-label-width="0.5cm"/>
      </text:list-level-style-number>
      <text:list-level-style-number text:level="3" style:num-format="i">
        <style:list-level-properties text:space-before="1.5cm" text:min-label-width="0.5cm"/>
      </text:list-level-style-number>
    </text:list-style>
  </office:automatic-styles>
  <office:body>
    <office:text>{body}</office:text>
  </office:body>
</office:document-content>"""


def _manifest_xml():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="{_MANIFEST}" manifest:version="1.2">
  <manifest:file-entry manifest:media-type="application/vnd.oasis.opendocument.text"
      manifest:version="1.2" manifest:full-path="/"/>
  <manifest:file-entry manifest:media-type="text/xml" manifest:full-path="content.xml"/>
  <manifest:file-entry manifest:media-type="text/xml" manifest:full-path="styles.xml"/>
</manifest:manifest>"""


def _build_zip(body_xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
        zf.writestr("content.xml", _content_xml(body_xml))
        zf.writestr("styles.xml", _styles_xml())
        zf.writestr("META-INF/manifest.xml", _manifest_xml())
    buf.seek(0)
    return buf.read()
