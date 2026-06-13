from unittest.mock import patch, MagicMock

import pytest

from app.shared.pandoc_formats import (
    get_attachment_actions,
    convert_to_html,
    convert_to_odf,
    PANDOC_EXTENSIONS,
    PANDOC_UPLOAD_EXTENSIONS,
)


class TestGetAttachmentActions:
    def test_pandoc_supported_format_docx(self):
        actions = get_attachment_actions("report.docx")
        assert actions["download"] is True
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions["target_type"] == "odt"
        assert actions["pandoc_reader"] == "docx"

    def test_pandoc_supported_format_rtf(self):
        actions = get_attachment_actions("document.rtf")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions["target_type"] == "odt"

    def test_pandoc_supported_format_html(self):
        actions = get_attachment_actions("page.html")
        assert actions["view"] is True
        assert actions["pandoc_reader"] == "html"

    def test_pandoc_supported_format_txt(self):
        actions = get_attachment_actions("notes.txt")
        assert actions["view"] is True
        assert actions["pandoc_reader"] == "plain"

    def test_pandoc_supported_format_md(self):
        actions = get_attachment_actions("readme.md")
        assert actions["view"] is True
        assert actions["pandoc_reader"] == "markdown"

    def test_spreadsheet_format_xlsx(self):
        actions = get_attachment_actions("data.xlsx")
        assert actions["view"] is False
        assert actions["open_in_docs"] is True
        assert actions["target_type"] == "ods"

    def test_presentation_format_pptx(self):
        actions = get_attachment_actions("slides.pptx")
        assert actions["view"] is False
        assert actions["open_in_docs"] is True
        assert actions["target_type"] == "odp"

    def test_native_odt(self):
        actions = get_attachment_actions("doc.odt")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions["target_type"] == "odt"

    def test_native_ods(self):
        actions = get_attachment_actions("sheet.ods")
        assert actions["view"] is False
        assert actions["open_in_docs"] is True
        assert actions["target_type"] == "ods"

    def test_unsupported_format(self):
        actions = get_attachment_actions("archive.zip")
        assert actions["download"] is True
        assert actions["view"] is False
        assert actions["open_in_docs"] is False

    def test_pdf_has_native_view_and_docs(self):
        actions = get_attachment_actions("doc.pdf")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions.get("native_view") is True
        assert actions["target_type"] == "odt"

    def test_image_jpg_native_view_only(self):
        actions = get_attachment_actions("photo.jpg")
        assert actions["view"] is True
        assert actions["open_in_docs"] is False
        assert actions.get("native_view") is True

    def test_image_png_native_view_only(self):
        actions = get_attachment_actions("image.png")
        assert actions["view"] is True
        assert actions["open_in_docs"] is False
        assert actions.get("native_view") is True

    def test_image_gif_native_view_only(self):
        actions = get_attachment_actions("animation.gif")
        assert actions["view"] is True
        assert actions["open_in_docs"] is False

    def test_csv_view_and_docs(self):
        actions = get_attachment_actions("data.csv")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions["pandoc_reader"] == "csv"
        assert actions["target_type"] == "odt"

    def test_tsv_view_and_docs(self):
        actions = get_attachment_actions("data.tsv")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions["pandoc_reader"] == "tsv"

    def test_ipynb_view_and_docs(self):
        actions = get_attachment_actions("notebook.ipynb")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions["pandoc_reader"] == "ipynb"

    def test_no_extension(self):
        actions = get_attachment_actions("README")
        assert actions["download"] is True
        assert actions["view"] is False
        assert actions["open_in_docs"] is False

    def test_case_insensitive(self):
        actions = get_attachment_actions("Report.DOCX")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True

    def test_epub_format(self):
        actions = get_attachment_actions("book.epub")
        assert actions["view"] is True
        assert actions["open_in_docs"] is True
        assert actions["pandoc_reader"] == "epub"

    def test_latex_format(self):
        actions = get_attachment_actions("paper.tex")
        assert actions["view"] is True
        assert actions["pandoc_reader"] == "latex"

    def test_rst_format(self):
        actions = get_attachment_actions("doc.rst")
        assert actions["view"] is True
        assert actions["pandoc_reader"] == "rst"


class TestConvertToHtml:
    def test_success(self):
        with patch("app.shared.pandoc_formats.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=b"<html><body>Hello</body></html>",
            )
            result = convert_to_html(b"Hello", "plain")
        assert result is not None
        assert "Hello" in result
        mock_run.assert_called_once_with(
            ["pandoc", "-f", "plain", "-t", "html", "--standalone"],
            input=b"Hello",
            capture_output=True,
            timeout=30,
        )

    def test_pandoc_failure(self):
        with patch("app.shared.pandoc_formats.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr=b"error",
            )
            result = convert_to_html(b"data", "plain")
        assert result is None

    def test_pandoc_not_found(self):
        with patch("app.shared.pandoc_formats.subprocess.run", side_effect=FileNotFoundError):
            result = convert_to_html(b"data", "plain")
        assert result is None

    def test_pandoc_timeout(self):
        import subprocess
        with patch("app.shared.pandoc_formats.subprocess.run", side_effect=subprocess.TimeoutExpired("pandoc", 30)):
            result = convert_to_html(b"data", "plain")
        assert result is None


class TestConvertToOdf:
    def test_success(self):
        with patch("app.shared.pandoc_formats.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=b"PK\x03\x04odt-data",
            )
            result = convert_to_odf(b"Hello", "plain", "odt")
        assert result is not None
        mock_run.assert_called_once_with(
            ["pandoc", "-f", "plain", "-t", "odt"],
            input=b"Hello",
            capture_output=True,
            timeout=30,
        )

    def test_pandoc_failure(self):
        with patch("app.shared.pandoc_formats.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr=b"error",
            )
            result = convert_to_odf(b"data", "plain", "odt")
        assert result is None

    def test_pandoc_not_found(self):
        with patch("app.shared.pandoc_formats.subprocess.run", side_effect=FileNotFoundError):
            result = convert_to_odf(b"data", "plain", "odt")
        assert result is None


class TestPandocExtensionsConfig:
    def test_all_entries_have_target_type(self):
        for ext, info in PANDOC_EXTENSIONS.items():
            assert "target_type" in info, f"Missing target_type for .{ext}"

    def test_all_viewable_have_pandoc_reader(self):
        for ext, info in PANDOC_EXTENSIONS.items():
            if info.get("view") and not info.get("native_view"):
                assert info.get("pandoc_reader"), f"Viewable .{ext} missing pandoc_reader"

    def test_upload_extensions_includes_all_pandoc(self):
        assert PANDOC_UPLOAD_EXTENSIONS == set(PANDOC_EXTENSIONS.keys())

    def test_common_formats_present(self):
        for ext in ["docx", "odt", "rtf", "html", "txt", "md", "xlsx", "pptx", "csv", "tsv", "ipynb"]:
            assert ext in PANDOC_EXTENSIONS, f"Missing .{ext}"
