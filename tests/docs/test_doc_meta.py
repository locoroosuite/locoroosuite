import io
import json
import os
import tempfile

import pytest

from app.modules.docs.services import doc_meta
from app.modules.docs.services.templates import empty_odt, empty_ods, empty_odp


@pytest.fixture
def odt_bytes():
    return empty_odt().read()


@pytest.fixture
def ods_bytes():
    return empty_ods().read()


@pytest.fixture
def odp_bytes():
    return empty_odp().read()


class TestInjectMetadata:
    def test_inject_and_extract_roundtrip(self, odt_bytes):
        metadata = {
            "id": "abc123",
            "name": "My Document",
            "doc_type": "odt",
            "account_id": 42,
            "deleted_at": None,
            "created_at": "2026-01-15 10:00:00",
            "updated_at": "2026-01-16 12:30:00",
        }
        patched = doc_meta.inject_metadata(odt_bytes, metadata)
        extracted = doc_meta.extract_metadata(patched)
        assert extracted == metadata

    def test_inject_preserves_zip_structure(self, odt_bytes):
        import zipfile
        metadata = {"id": "x", "name": "Y", "doc_type": "odt", "account_id": 1}
        patched = doc_meta.inject_metadata(odt_bytes, metadata)
        with zipfile.ZipFile(io.BytesIO(patched), "r") as zf:
            names = zf.namelist()
            assert "meta.xml" in names
            assert "content.xml" in names
            assert "mimetype" in names
            assert "styles.xml" in names

    def test_inject_overwrites_existing_metadata(self, odt_bytes):
        metadata_v1 = {"id": "a", "name": "v1", "doc_type": "odt", "account_id": 1}
        metadata_v2 = {"id": "a", "name": "v2", "doc_type": "odt", "account_id": 1}
        patched = doc_meta.inject_metadata(odt_bytes, metadata_v1)
        patched = doc_meta.inject_metadata(patched, metadata_v2)
        extracted = doc_meta.extract_metadata(patched)
        assert extracted["name"] == "v2"

    def test_inject_with_unicode_name(self, odt_bytes):
        metadata = {"id": "u1", "name": "Dokumentación", "doc_type": "odt", "account_id": 1}
        patched = doc_meta.inject_metadata(odt_bytes, metadata)
        extracted = doc_meta.extract_metadata(patched)
        assert extracted["name"] == "Dokumentación"

    def test_inject_with_deleted_at(self, odt_bytes):
        metadata = {
            "id": "d1", "name": "Trashed", "doc_type": "odt", "account_id": 1,
            "deleted_at": "2026-05-20 08:00:00",
        }
        patched = doc_meta.inject_metadata(odt_bytes, metadata)
        extracted = doc_meta.extract_metadata(patched)
        assert extracted["deleted_at"] == "2026-05-20 08:00:00"

    def test_inject_on_spreadsheet(self, ods_bytes):
        metadata = {"id": "s1", "name": "Sheet", "doc_type": "ods", "account_id": 1}
        patched = doc_meta.inject_metadata(ods_bytes, metadata)
        extracted = doc_meta.extract_metadata(patched)
        assert extracted["doc_type"] == "ods"

    def test_inject_on_presentation(self, odp_bytes):
        metadata = {"id": "p1", "name": "Deck", "doc_type": "odp", "account_id": 1}
        patched = doc_meta.inject_metadata(odp_bytes, metadata)
        extracted = doc_meta.extract_metadata(patched)
        assert extracted["doc_type"] == "odp"

    def test_inject_collabora_roundtrip(self, odt_bytes):
        metadata = {"id": "c1", "name": "Test", "doc_type": "odt", "account_id": 1}
        patched = doc_meta.inject_metadata(odt_bytes, metadata)
        import zipfile
        with zipfile.ZipFile(io.BytesIO(patched), "r") as zf:
            content = zf.read("content.xml")
            assert b"office:text" in content
        new_metadata = {"id": "c1", "name": "Test", "doc_type": "odt", "account_id": 1}
        patched2 = doc_meta.inject_metadata(patched, new_metadata)
        extracted = doc_meta.extract_metadata(patched2)
        assert extracted["id"] == "c1"


class TestExtractMetadata:
    def test_extract_from_file_without_metadata(self, odt_bytes):
        result = doc_meta.extract_metadata(odt_bytes)
        assert result is None

    def test_extract_from_invalid_bytes(self):
        result = doc_meta.extract_metadata(b"not a zip file")
        assert result is None

    def test_extract_from_zip_without_meta_xml(self):
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("content.xml", "<content/>")
        buf.seek(0)
        result = doc_meta.extract_metadata(buf.read())
        assert result is None

    def test_extract_from_meta_xml_without_locoroo_field(self):
        import zipfile
        meta_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" office:version="1.2">'
            '<meta:meta>'
            '<meta:user-defined meta:name="dc:title">Some Title</meta:user-defined>'
            '</meta:meta>'
            '</office:document-meta>'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("meta.xml", meta_xml)
        buf.seek(0)
        result = doc_meta.extract_metadata(buf.read())
        assert result is None

    def test_extract_handles_corrupted_json(self):
        import zipfile
        meta_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" office:version="1.2">'
            '<meta:meta>'
            '<meta:user-defined meta:name="x-locoroo-meta">{not valid json}</meta:user-defined>'
            '</meta:meta>'
            '</office:document-meta>'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("meta.xml", meta_xml)
        buf.seek(0)
        result = doc_meta.extract_metadata(buf.read())
        assert result is None


class TestEmptyTemplatesHaveMetaXml:
    def test_empty_odt_has_meta_xml(self):
        data = empty_odt().read()
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            assert "meta.xml" in zf.namelist()

    def test_empty_ods_has_meta_xml(self):
        data = empty_ods().read()
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            assert "meta.xml" in zf.namelist()

    def test_empty_odp_has_meta_xml(self):
        data = empty_odp().read()
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            assert "meta.xml" in zf.namelist()

    def test_empty_template_extract_returns_none(self):
        data = empty_odt().read()
        assert doc_meta.extract_metadata(data) is None
