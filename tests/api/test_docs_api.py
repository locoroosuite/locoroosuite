import io
import json
import os
import shutil
import tempfile

import pytest


def _create_token(app, customer_id, dek_hex="a" * 64, name="test-token", scopes=None):
    from app.api.token_service import create_api_token
    if scopes is None:
        scopes = ["docs:read", "docs:write"]
    return create_api_token(customer_id, dek_hex, name, scopes)


def _auth_header(token_value):
    return {"Authorization": f"Bearer {token_value}"}


def _read_content_xml(data):
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("content.xml").decode("utf-8")


def _read_styles_xml(data):
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("styles.xml").decode("utf-8")


def _setup_env(app, account_id):
    with app.app_context():
        from app.shared.db import db
        from app.shared.models.core import CustomerAccount
        account = db.session.get(CustomerAccount, account_id)
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cache_path = f.name
        f.close()
        account.cache_db_path = cache_path
        db.session.commit()
    return cache_path


@pytest.fixture()
def docs_api(app, api_customer):
    client, user_id, account_id = api_customer
    with app.app_context():
        token_value, _ = _create_token(app, user_id)
    cache_path = _setup_env(app, account_id)
    docs_dir = tempfile.mkdtemp(prefix="docs_test_")
    app.config["DOCS_DIR"] = docs_dir
    from app.api.openapi import api_app
    api_app.config["DOCS_DIR"] = docs_dir
    yield client, token_value, account_id, cache_path
    try:
        os.unlink(cache_path)
    except OSError:
        pass
    shutil.rmtree(docs_dir, ignore_errors=True)


class TestListDocuments:
    def test_empty_list(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.get("/api/v1/docs/documents", headers=_auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []
        assert data["pagination"]["has_more"] is False

    def test_returns_created_document(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Test Doc", "type": "odt"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201

        resp = client.get("/api/v1/docs/documents", headers=_auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        doc = data["data"][0]
        assert doc["name"] == "Test Doc"
        assert doc["type"] == "odt"
        assert "id" in doc
        assert "created_at" in doc
        assert "updated_at" in doc

    def test_respects_limit(self, app, docs_api):
        client, token, account_id, _ = docs_api
        for i in range(5):
            client.post(
                "/api/v1/docs/documents",
                json={"name": f"Doc {i}", "type": "odt"},
                headers=_auth_header(token),
            )

        resp = client.get("/api/v1/docs/documents?max_results=2", headers=_auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        assert data["pagination"]["has_more"] is True

    def test_excludes_deleted(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "To Delete", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        client.delete(f"/api/v1/docs/documents/{doc_id}", headers=_auth_header(token))

        resp = client.get("/api/v1/docs/documents", headers=_auth_header(token))
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_search_not_supported(self, app, docs_api):
        client, token, account_id, _ = docs_api
        client.post(
            "/api/v1/docs/documents",
            json={"name": "Budget Report", "type": "odt"},
            headers=_auth_header(token),
        )
        client.post(
            "/api/v1/docs/documents",
            json={"name": "Meeting Notes", "type": "odt"},
            headers=_auth_header(token),
        )

        resp = client.get("/api/v1/docs/documents", headers=_auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2


class TestGetDocument:
    def test_get_existing(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "My Doc", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.get(f"/api/v1/docs/documents/{doc_id}", headers=_auth_header(token))
        assert resp.status_code == 200
        doc = json.loads(resp.data)["data"]
        assert doc["id"] == doc_id
        assert doc["name"] == "My Doc"
        assert doc["type"] == "odt"

    def test_get_nonexistent(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.get("/api/v1/docs/documents/nonexistent-id", headers=_auth_header(token))
        assert resp.status_code == 404


class TestCreateDocument:
    def test_create_odt(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "New Doc", "type": "odt"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        doc = json.loads(resp.data)["data"]
        assert doc["name"] == "New Doc"
        assert doc["type"] == "odt"
        assert doc["size"] == 0
        assert "id" in doc

    def test_create_returns_consistent_schema(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Schema Doc", "type": "odt"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        doc = json.loads(resp.data)["data"]
        for key in ("id", "name", "type", "size", "created_at", "updated_at"):
            assert key in doc, f"Missing field: {key}"
        assert isinstance(doc["size"], int)
        assert doc["created_at"] is not None
        assert doc["updated_at"] is not None

    def test_create_ods(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Spreadsheet", "type": "ods"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        assert json.loads(resp.data)["data"]["type"] == "ods"

    def test_create_odp(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Presentation", "type": "odp"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        assert json.loads(resp.data)["data"]["type"] == "odp"

    def test_create_invalid_type(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Bad Type", "type": "exe"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 400

    def test_create_default_name_and_type(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        doc = json.loads(resp.data)["data"]
        assert doc["name"] == "Untitled Document"
        assert doc["type"] == "odt"

    def test_creates_storage_file(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Stored Doc", "type": "odt"},
            headers=_auth_header(token),
        )
        doc = json.loads(resp.data)["data"]
        with app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.modules.docs.services.storage import file_exists
            account = CustomerAccount.query.filter_by(id=account_id).first()
            assert file_exists(account.customer_id, account_id, doc["id"])


class TestDeleteDocument:
    def test_soft_delete(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "To Delete", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.delete(f"/api/v1/docs/documents/{doc_id}", headers=_auth_header(token))
        assert resp.status_code == 204

        resp = client.get("/api/v1/docs/documents", headers=_auth_header(token))
        data = json.loads(resp.data)
        assert all(d["id"] != doc_id for d in data["data"])

    def test_delete_nonexistent(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.delete("/api/v1/docs/documents/nonexistent", headers=_auth_header(token))
        assert resp.status_code == 404


class TestRenameDocument:
    def test_rename(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Original", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.put(
            f"/api/v1/docs/documents/{doc_id}",
            json={"name": "Renamed"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        assert json.loads(resp.data)["data"]["name"] == "Renamed"

    def test_rename_returns_consistent_schema(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Schema Rename", "type": "ods"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.put(
            f"/api/v1/docs/documents/{doc_id}",
            json={"name": "Renamed Doc"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        doc = json.loads(resp.data)["data"]
        for key in ("id", "name", "type", "size", "created_at", "updated_at"):
            assert key in doc, f"Missing field: {key}"
        assert doc["name"] == "Renamed Doc"
        assert doc["type"] == "ods"

    def test_rename_empty_name(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Original", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.put(
            f"/api/v1/docs/documents/{doc_id}",
            json={"name": "  "},
            headers=_auth_header(token),
        )
        assert resp.status_code == 400

    def test_rename_nonexistent(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.put(
            "/api/v1/docs/documents/nonexistent",
            json={"name": "New Name"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 404


class TestDownloadDocument:
    def test_download_odt(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Download Me", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.get(f"/api/v1/docs/documents/{doc_id}/download", headers=_auth_header(token))
        assert resp.status_code == 200
        assert resp.content_type == "application/vnd.oasis.opendocument.text"
        assert len(resp.data) > 0

    def test_download_nonexistent(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.get("/api/v1/docs/documents/nonexistent/download", headers=_auth_header(token))
        assert resp.status_code == 404


class TestReadContent:
    def test_read_content_text(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Content Doc", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.get(f"/api/v1/docs/documents/{doc_id}/content?format=text", headers=_auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "content" in data
        assert data["format"] == "text"

    def test_read_content_nonexistent(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.get("/api/v1/docs/documents/nonexistent/content", headers=_auth_header(token))
        assert resp.status_code == 404


class TestUpdateContent:
    def test_update_via_json(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Update Me", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.put(
            f"/api/v1/docs/documents/{doc_id}/content",
            json={"content": "# Hello World", "format": "markdown"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"]["id"] == doc_id
        assert data["data"]["size"] > 0

    def test_update_content_returns_consistent_schema(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Content Schema", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.put(
            f"/api/v1/docs/documents/{doc_id}/content",
            json={"content": "# Schema Test", "format": "markdown"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        doc = json.loads(resp.data)["data"]
        for key in ("id", "name", "type", "size", "created_at", "updated_at"):
            assert key in doc, f"Missing field: {key}"
        assert doc["size"] > 0

    def test_update_nonexistent(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.put(
            "/api/v1/docs/documents/nonexistent/content",
            json={"content": "text"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 404


class TestMarkdownConversion:
    """Tests for markdown→ODT conversion via pandoc."""

    def _update_and_read(self, app, docs_api, markdown_content):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "MD Test", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.put(
            f"/api/v1/docs/documents/{doc_id}/content",
            json={"content": markdown_content, "format": "markdown"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200

        from app.modules.docs.services.storage import read_file
        with app.app_context():
            from app.shared.models.core import CustomerAccount
            account = CustomerAccount.query.filter_by(id=account_id).first()
            return read_file(account.customer_id, account_id, doc_id)

    def test_produces_valid_odt(self, app, docs_api):
        data = self._update_and_read(app, docs_api, "Hello world")
        assert data[:2] == b"PK"
        content = _read_content_xml(data)
        assert "Hello world" in content

    def test_heading_rendered(self, app, docs_api):
        data = self._update_and_read(app, docs_api, "# Title")
        content = _read_content_xml(data)
        assert "Title" in content

    def test_numbered_list(self, app, docs_api):
        md = "1. First item\n2. Second item\n3. Third item"
        data = self._update_and_read(app, docs_api, md)
        content = _read_content_xml(data)
        assert "First item" in content
        assert "Second item" in content
        assert "<text:list" in content

    def test_bulleted_list(self, app, docs_api):
        md = "- Alpha\n- Beta\n- Gamma"
        data = self._update_and_read(app, docs_api, md)
        content = _read_content_xml(data)
        assert "Alpha" in content
        assert "Beta" in content
        assert "<text:list" in content

    def test_separate_numbered_and_bullet_lists(self, app, docs_api):
        md = "1. First\n2. Second\n\n- Bullet A\n- Bullet B"
        data = self._update_and_read(app, docs_api, md)
        content = _read_content_xml(data)
        assert "First" in content
        assert "Bullet A" in content

    def test_bold_and_italic(self, app, docs_api):
        md = "Text with **bold** and *italic* words"
        data = self._update_and_read(app, docs_api, md)
        content = _read_content_xml(data)
        assert "bold" in content
        assert "italic" in content

    def test_table_rendered(self, app, docs_api):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        data = self._update_and_read(app, docs_api, md)
        content = _read_content_xml(data)
        assert "<table:table" in content

    def test_blockquote_rendered(self, app, docs_api):
        md = "> This is a quote"
        data = self._update_and_read(app, docs_api, md)
        content = _read_content_xml(data)
        assert "This is a quote" in content

    def test_no_html_master_page(self, app, docs_api):
        data = self._update_and_read(app, docs_api, "Hello")
        content = _read_content_xml(data)
        assert 'master-page-name="HTML"' not in content

    def test_has_page_layout(self, app, docs_api):
        data = self._update_and_read(app, docs_api, "Hello")
        styles = _read_styles_xml(data)
        assert "page-layout" in styles


class TestDrafts:
    def test_create_and_list_drafts(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Original", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        resp = client.post(
                f"/api/v1/docs/documents/{doc_id}/drafts",
                json={"content": "Draft content", "summary": "AI edit"},
                headers=_auth_header(token),
            )
        assert resp.status_code == 201
        draft = json.loads(resp.data)["data"]
        assert "id" in draft
        assert draft["source_document_id"] == doc_id
        assert draft["summary"] == "AI edit"
        draft_id = draft["id"]

        resp = client.get(f"/api/v1/docs/documents/{doc_id}/drafts", headers=_auth_header(token))
        assert resp.status_code == 200
        drafts = json.loads(resp.data)["data"]
        assert len(drafts) >= 1
        assert any(d["id"] == draft_id for d in drafts)

    def test_apply_draft(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Original", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        draft_resp = client.post(
                f"/api/v1/docs/documents/{doc_id}/drafts",
                json={"content": "Draft v2", "summary": "v2 edit"},
                headers=_auth_header(token),
            )
        draft_id = json.loads(draft_resp.data)["data"]["id"]

        resp = client.post(
            f"/api/v1/docs/documents/{doc_id}/drafts/{draft_id}/apply",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        assert json.loads(resp.data)["data"]["id"] == doc_id

    def test_discard_draft(self, app, docs_api):
        client, token, account_id, _ = docs_api
        create_resp = client.post(
            "/api/v1/docs/documents",
            json={"name": "Original", "type": "odt"},
            headers=_auth_header(token),
        )
        doc_id = json.loads(create_resp.data)["data"]["id"]

        draft_resp = client.post(
                f"/api/v1/docs/documents/{doc_id}/drafts",
                json={"content": "Discard me", "summary": "discard"},
                headers=_auth_header(token),
            )
        draft_id = json.loads(draft_resp.data)["data"]["id"]

        resp = client.delete(
            f"/api/v1/docs/documents/{doc_id}/drafts/{draft_id}",
            headers=_auth_header(token),
        )
        assert resp.status_code == 204

    def test_create_draft_nonexistent_doc(self, app, docs_api):
        client, token, account_id, _ = docs_api
        resp = client.post(
            "/api/v1/docs/documents/nonexistent/drafts",
            json={"content": "text"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 404


class TestScopeEnforcement:
    def test_read_only_cannot_create(self, app, api_customer):
        client, user_id, account_id = api_customer
        cache_path = _setup_env(app, account_id)
        try:
            with app.app_context():
                token_value, _ = _create_token(app, user_id, scopes=["docs:read"])
            resp = client.post(
                "/api/v1/docs/documents",
                json={"name": "Blocked", "type": "odt"},
                headers=_auth_header(token_value),
            )
            assert resp.status_code == 403
        finally:
            os.unlink(cache_path)

    def test_write_scope_can_read(self, app, api_customer):
        client, user_id, account_id = api_customer
        cache_path = _setup_env(app, account_id)
        try:
            with app.app_context():
                token_value, _ = _create_token(app, user_id, scopes=["docs:write"])
            resp = client.get("/api/v1/docs/documents", headers=_auth_header(token_value))
            assert resp.status_code == 200
        finally:
            os.unlink(cache_path)

    def test_no_docs_scope_cannot_access(self, app, api_customer):
        client, user_id, account_id = api_customer
        cache_path = _setup_env(app, account_id)
        try:
            with app.app_context():
                token_value, _ = _create_token(app, user_id, scopes=["mail:read"])
            resp = client.get("/api/v1/docs/documents", headers=_auth_header(token_value))
            assert resp.status_code == 403
        finally:
            os.unlink(cache_path)
