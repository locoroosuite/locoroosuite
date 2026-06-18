import re
import uuid
from pathlib import Path

import pytest

from tests.e2e.conftest import skip_if_no_services

_PDF_FIXTURE = Path(__file__).parent / "fixtures" / "sample.pdf"


@skip_if_no_services
class TestDocsList:
    def test_docs_list_loads(self, app_url, user_session):
        r = user_session.get(f"{app_url}/app/docs/", allow_redirects=True)
        assert r.status_code == 200


@skip_if_no_services
class TestDocsCRUD:
    def test_create_rename_edit_delete_document(self, app_url, user_session):
        r = user_session.post(
            f"{app_url}/app/docs/new",
            data={"doc_type": "odt"},
            allow_redirects=False,
        )
        assert r.status_code in (302, 303)
        location = r.headers.get("Location", "")
        doc_id_match = re.search(r"/docs/([a-f0-9]+)/edit", location)
        assert doc_id_match, f"Could not extract doc_id from redirect: {location}"
        doc_id = doc_id_match.group(1)

        r = user_session.get(f"{app_url}/app/docs/", allow_redirects=True)
        assert r.status_code == 200
        assert doc_id in r.text

        new_name = f"E2E Doc {uuid.uuid4().hex[:8]}"
        r = user_session.post(
            f"{app_url}/app/docs/{doc_id}/rename",
            data={"name": new_name},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("name") == new_name

        r = user_session.get(f"{app_url}/app/docs/", allow_redirects=True)
        assert r.status_code == 200
        assert new_name in r.text

        r = user_session.get(
            f"{app_url}/app/docs/{doc_id}/edit",
            allow_redirects=True,
        )
        assert r.status_code == 200
        assert "iframe" in r.text.lower() or "collabora" in r.text.lower()

        r = user_session.post(
            f"{app_url}/app/docs/{doc_id}/delete",
            allow_redirects=True,
        )
        assert r.status_code == 200

        r = user_session.post(
            f"{app_url}/app/docs/{doc_id}/delete",
            allow_redirects=True,
        )
        assert r.status_code == 200

        r = user_session.get(f"{app_url}/app/docs/", allow_redirects=True)
        assert r.status_code == 200
        assert new_name not in r.text


@skip_if_no_services
class TestDocsPdfConvert:
    """End-to-end PDF -> editable ODF conversion through the real Collabora.

    Regression guard for the bug where PDFs were converted to ``odt`` and
    Collabora rejected the save (HTTP 401 / X-ERROR-KIND: savefailed) because
    LibreOffice imports PDFs as Draw documents. The only valid ODF target for a
    PDF source is ``odg``.
    """

    def test_upload_pdf_then_convert_to_editable(self, app_url, user_session):
        with open(_PDF_FIXTURE, "rb") as f:
            up = user_session.post(
                f"{app_url}/app/docs/upload",
                files={"file": ("e2e_sample.pdf", f, "application/pdf")},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert up.status_code == 200, up.text
        up_body = up.json()
        assert "doc_id" in up_body
        original_id = up_body["doc_id"]

        conv = user_session.post(
            f"{app_url}/app/docs/{original_id}/convert",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert conv.status_code == 200, conv.text
        conv_body = conv.json()
        assert "doc_id" in conv_body
        new_id = conv_body["doc_id"]
        assert new_id != original_id

        # The converted document must open in the Collabora editor.
        edit = user_session.get(f"{app_url}/app/docs/{new_id}/edit", allow_redirects=True)
        assert edit.status_code == 200
        assert "iframe" in edit.text.lower() or "collabora" in edit.text.lower()

        # And appear in the docs list as an editable (non-original) drawing.
        lst = user_session.get(f"{app_url}/app/docs/", allow_redirects=True)
        assert lst.status_code == 200
        assert new_id in lst.text
