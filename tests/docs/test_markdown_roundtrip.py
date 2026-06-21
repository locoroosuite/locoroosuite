from __future__ import annotations


from app.modules.docs.services.markdown_to_odt import convert
from app.api.controllers.docs import _extract_odt_text


def _roundtrip(md: str, tmp_path) -> str:
    odt_bytes = convert(md)
    odt_file = tmp_path / "test.odt"
    odt_file.write_bytes(odt_bytes)
    return _extract_odt_text(odt_file)


def test_simple_h1(tmp_path):
    text = _roundtrip("# Title", tmp_path)
    assert "Title" in text


def test_multiple_heading_levels(tmp_path):
    text = _roundtrip("# H1\n## H2\n### H3", tmp_path)
    assert "H1" in text
    assert "H2" in text
    assert "H3" in text


def test_heading_with_bold(tmp_path):
    text = _roundtrip("# **Bold Title**", tmp_path)
    assert "Bold Title" in text


def test_mixed_headings_and_paragraphs(tmp_path):
    md = "# Introduction\nSome intro text.\n## Details\nMore details here.\n### Subsection\nEven more.\n"
    text = _roundtrip(md, tmp_path)
    assert "Introduction" in text
    assert "Some intro text" in text
    assert "Details" in text
    assert "More details here" in text
    assert "Subsection" in text
    assert "Even more" in text
