"""Unit tests for the Collabora service layer.

These exercise ``collabora._convert`` / ``_is_valid_output`` / ``_mime_for_ext``
directly (mocking ``requests``). Previously this module had no tests at all — it
was only ever mocked at controller call sites, which hid the PDF conversion bug.
"""
import io
from unittest.mock import patch, MagicMock

import pytest

from app.modules.docs.services import collabora
from app.modules.docs.services.collabora import (
    ConversionError, _convert, _is_valid_output, _mime_for_ext, get_edit_url,
)


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    collabora._discovery_cache.clear()
    yield
    collabora._discovery_cache.clear()


def _mock_response(content=b"PK\x03\x04odt-data", status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


class TestConvertRequestShape:
    def test_convert_posts_to_cool_convert_with_target_format(self, app):
        app.config["COLLABORA_INTERNAL_URL"] = "http://cool:9980"
        with patch("app.modules.docs.services.collabora.requests.post", return_value=_mock_response()) as mock_post:
            _convert(io.BytesIO(b"%PDF-1.4 data"), "contract.pdf", "odg")

        url, *args = mock_post.call_args.args
        assert url == "http://cool:9980/cool/convert-to"
        kwargs = mock_post.call_args.kwargs
        assert kwargs["data"] == {"format": "odg"}
        assert kwargs["timeout"] == 120
        files = kwargs["files"]["data"]
        # (filename, raw_bytes, mime)
        assert files[0] == "contract.pdf"
        assert files[1] == b"%PDF-1.4 data"
        assert files[2] == "application/pdf"

    def test_convert_uses_internal_url_over_default(self, app):
        app.config["COLLABORA_INTERNAL_URL"] = "http://internal:9980"
        app.config["COLLABORA_URL"] = "http://fallback:9980"
        with patch("app.modules.docs.services.collabora.requests.post", return_value=_mock_response()) as mock_post:
            _convert(io.BytesIO(b"PK\x03\x04x"), "d.odt", "odt")
        assert mock_post.call_args.args[0] == "http://internal:9980/cool/convert-to"

    def test_convert_returns_bytesio(self, app):
        app.config["COLLABORA_INTERNAL_URL"] = "http://cool:9980"
        with patch("app.modules.docs.services.collabora.requests.post",
                   return_value=_mock_response(content=b"PK\x03\x04payload")):
            result = _convert(io.BytesIO(b"data"), "d.odt", "odt")
        assert isinstance(result, io.BytesIO)
        assert result.read() == b"PK\x03\x04payload"

    def test_convert_reads_bytes_or_filestream(self, app):
        app.config["COLLABORA_INTERNAL_URL"] = "http://cool:9980"
        # Passing raw bytes (not a stream) must also work.
        with patch("app.modules.docs.services.collabora.requests.post", return_value=_mock_response()) as mock_post:
            _convert(b"raw-bytes", "d.odt", "odt")
        assert mock_post.call_args.kwargs["files"]["data"][1] == b"raw-bytes"


class TestConvertErrors:
    def test_convert_raises_on_http_error(self, app):
        import requests
        app.config["COLLABORA_INTERNAL_URL"] = "http://cool:9980"
        with patch("app.modules.docs.services.collabora.requests.post",
                   return_value=_mock_response(status=401, content=b"")):
            resp = MagicMock()
            resp.raise_for_status.side_effect = requests.HTTPError("401 Client Error")
            with patch("app.modules.docs.services.collabora.requests.post", return_value=resp):
                with pytest.raises(ConversionError, match="Collabora conversion failed"):
                    _convert(io.BytesIO(b"%PDF-1.4 data"), "c.pdf", "odg")

    def test_convert_raises_when_url_unconfigured(self, app):
        app.config["COLLABORA_INTERNAL_URL"] = None
        app.config["COLLABORA_URL"] = None
        with pytest.raises(ConversionError, match="Collabora URL not configured"):
            _convert(io.BytesIO(b"x"), "d.odt", "odt")

    def test_convert_raises_on_invalid_output_magic(self, app):
        # Collabora sometimes returns 200 with an error body (e.g. empty / HTML
        # error). Invalid magic must raise rather than silently storing junk.
        app.config["COLLABORA_INTERNAL_URL"] = "http://cool:9980"
        with patch("app.modules.docs.services.collabora.requests.post",
                   return_value=_mock_response(content=b"ERROR: save failed")):
            with pytest.raises(ConversionError, match="invalid output"):
                _convert(io.BytesIO(b"%PDF-1.4 data"), "c.pdf", "odg")


class TestIsValidOutput:
    @pytest.mark.parametrize("target", ["odt", "ods", "odp", "odg"])
    def test_odf_targets_require_zip_magic(self, target):
        assert _is_valid_output(b"PK\x03\x04rest", target) is True

    @pytest.mark.parametrize("target", ["odt", "ods", "odp", "odg"])
    def test_odf_targets_reject_pdf_magic(self, target):
        assert _is_valid_output(b"%PDF-1.4 data", target) is False

    def test_pdf_target_requires_pdf_magic(self):
        assert _is_valid_output(b"%PDF-1.4 data", "pdf") is True
        assert _is_valid_output(b"PK\x03\x04data", "pdf") is False

    def test_rejects_empty(self):
        assert _is_valid_output(b"", "odt") is False

    def test_rejects_too_short(self):
        assert _is_valid_output(b"PK", "odt") is False


class TestMimeForExt:
    def test_pdf_returns_pdf_mime(self):
        assert _mime_for_ext("pdf") == "application/pdf"

    def test_odg_returns_graphics_mime(self):
        assert _mime_for_ext("odg") == "application/vnd.oasis.opendocument.graphics"

    def test_office_formats(self):
        assert _mime_for_ext("docx").startswith("application/vnd")
        assert _mime_for_ext("odt") == "application/vnd.oasis.opendocument.text"

    def test_text_formats(self):
        assert _mime_for_ext("html") == "text/html"
        assert _mime_for_ext("csv") == "text/csv"
        assert _mime_for_ext("txt") == "text/plain"

    def test_unknown_returns_octet_stream(self):
        assert _mime_for_ext("zip") == "application/octet-stream"

    def test_case_insensitive_via_extension(self):
        # _mime_for_ext receives a lowercased ext from _extension(); verify the
        # lookup tolerates the values produced by the caller.
        assert _mime_for_ext("PDF".lower()) == "application/pdf"


class TestGetEditUrl:
    def test_odg_edit_url_resolved_from_discovery(self, app):
        app.config["COLLABORA_INTERNAL_URL"] = "http://cool:9980"
        discovery = (
            '<wopi-discovery><net-zone name="external-http">'
            '<app name="drawing"><action ext="odg" name="edit" '
            'urlsrc="http://cool:9980/browser/d/cool.html?"/></app>'
            '</net-zone></wopi-discovery>'
        )
        resp = MagicMock(status_code=200, text=discovery)
        resp.raise_for_status = MagicMock()
        with patch("app.modules.docs.services.collabora.requests.get", return_value=resp):
            url = get_edit_url("odg")
        assert url == "http://cool:9980/browser/d/cool.html?"

    def test_odt_ods_odp_odg_all_resolvable(self, app):
        app.config["COLLABORA_INTERNAL_URL"] = "http://cool:9980"
        actions = "".join(
            f'<app name="{a}"><action ext="{e}" name="edit" urlsrc="http://cool:9980/u?{e}="/></app>'
            for a, e in (("text", "odt"), ("spreadsheet", "ods"),
                         ("presentation", "odp"), ("drawing", "odg"))
        )
        discovery = f'<wopi-discovery><net-zone name="x">{actions}</net-zone></wopi-discovery>'
        resp = MagicMock(status_code=200, text=discovery)
        resp.raise_for_status = MagicMock()
        with patch("app.modules.docs.services.collabora.requests.get", return_value=resp):
            for ext in ("odt", "ods", "odp", "odg"):
                assert get_edit_url(ext) is not None
