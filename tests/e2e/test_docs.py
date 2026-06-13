import re
import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services


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
