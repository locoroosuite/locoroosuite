from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from app.api.token_service import create_api_token, generate_dek
from app.shared.db import db as _db
from app.shared.keys import clear_user_key, set_user_key
from app.shared.models.core import CustomerAccount, Domain, User

DOCS = "app.mcp.tools.docs"
DOCS_CACHE_DB = "app.modules.docs.services.cache_db"
DOCS_STORAGE = "app.modules.docs.services.storage"
DOCS_TEMPLATES = "app.modules.docs.services.templates"
UI_EVENTS = "app.shared.ui_events"


@pytest.fixture()
def mcp_docs(app, _clean_db):
    user_id = None
    account_id = None

    with app.app_context():
        user = User(email="mcp-docs@example.com", role="customer", is_active=True)  # type: ignore[call-arg]
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain(
            name="example.com",
            is_active=True,
            status="active",
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        _db.session.add(domain)
        _db.session.flush()

        dek = generate_dek()
        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address="mcp-docs@example.com",
            auth_type="password",
            username="mcp-docs@example.com",
            cache_db_path="",
            api_enabled=True,
            dek_wrapped_cred=b"placeholder",
            is_active=True,
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    token_value = None
    with app.app_context():
        token_value, _ = create_api_token(user_id, dek, "test-token", ["docs:read", "docs:write"])

    from app.mcp.auth import set_current_token

    set_current_token(token_value)

    mcp = FastMCP("test-docs")
    from app.mcp.tools.docs import register as register_docs

    register_docs(mcp, app)

    tools = mcp._tool_manager._tools

    yield {
        "app": app,
        "tools": tools,
        "user_id": user_id,
        "account_id": account_id,
    }

    set_current_token("")
    clear_user_key(user_id)


def _mock_conn():
    conn = MagicMock()
    conn.close = MagicMock()
    return conn


def _mock_doc_row(
    doc_id,
    name="Test Doc",
    doc_type="odt",
    file_size=0,
    created_at="2025-01-01T00:00:00",
    updated_at="2025-01-01T00:00:00",
    deleted_at=None,
    account_id=1,
    original_format=None,
):
    keys = [
        "id",
        "name",
        "doc_type",
        "original_format",
        "file_size",
        "account_id",
        "created_at",
        "updated_at",
        "deleted_at",
    ]
    vals = {
        "id": doc_id,
        "name": name,
        "doc_type": doc_type,
        "original_format": original_format,
        "file_size": file_size,
        "account_id": account_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "deleted_at": deleted_at,
    }
    r = MagicMock()
    r.__getitem__ = lambda self, k: vals[k]
    r.keys = lambda: keys
    r.get = lambda k, default=None: vals.get(k, default)
    return r


class TestDocsListTools:
    def test_list_documents(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("doc-1", "Report.odt", file_size=1024)
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.list_documents", return_value=[mock_row]):
                result = asyncio.run(tools["docs_list_documents"].fn())
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["id"] == "doc-1"
        assert data[0]["name"] == "Report.odt"
        assert data[0]["type"] == "odt"
        assert data[0]["size"] == 1024

    def test_list_documents_empty(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.list_documents", return_value=[]):
                result = asyncio.run(tools["docs_list_documents"].fn())
        data = json.loads(result)["data"]
        assert data == []

    def test_list_documents_with_type_filter(self, mcp_docs):
        tools = mcp_docs["tools"]
        odt_row = _mock_doc_row("doc-1", "Report.odt", doc_type="odt")
        ods_row = _mock_doc_row("doc-2", "Sheet.ods", doc_type="ods")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.list_documents", return_value=[odt_row, ods_row]):
                result = asyncio.run(tools["docs_list_documents"].fn(type="ods"))
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["type"] == "ods"

    def test_list_documents_with_search(self, mcp_docs):
        tools = mcp_docs["tools"]
        row1 = _mock_doc_row("doc-1", "Quarterly Report")
        row2 = _mock_doc_row("doc-2", "Meeting Notes")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.list_documents", return_value=[row1, row2]):
                result = asyncio.run(tools["docs_list_documents"].fn(search="report"))
        data = json.loads(result)["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Quarterly Report"

    def test_get_document(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("doc-1", "Report.odt", file_size=2048)
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=mock_row):
                result = asyncio.run(tools["docs_get_document"].fn(document_id="doc-1"))
        data = json.loads(result)["data"]
        assert data["id"] == "doc-1"
        assert data["name"] == "Report.odt"
        assert data["size"] == 2048
        assert data["created_at"] == "2025-01-01T00:00:00"
        assert data["updated_at"] == "2025-01-01T00:00:00"

    def test_get_document_not_found(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=None):
                with patch(f"{DOCS_CACHE_DB}.get_document", return_value=None):
                    result = asyncio.run(tools["docs_get_document"].fn(document_id="nonexistent"))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"


class TestDocsCreateDocument:
    def test_create_document(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("new-doc", "New Doc.odt", doc_type="odt", file_size=8000)
        mock_buf = MagicMock()
        mock_buf.read.return_value = b"\x00" * 8000
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.create_document"):
                with patch(f"{DOCS_TEMPLATES}.empty_odt", return_value=mock_buf):
                    with patch(
                        "app.modules.docs.services.doc_meta.inject_metadata",
                        side_effect=lambda data, metadata: data,
                    ):
                        with patch(f"{DOCS_STORAGE}.write_file"):
                            with patch(f"{DOCS_CACHE_DB}.update_file_size"):
                                with patch(
                                    f"{DOCS_CACHE_DB}.get_active_document", return_value=mock_row
                                ):
                                    with patch(f"{UI_EVENTS}.push_ui_event"):
                                        result = asyncio.run(
                                            tools["docs_create_document"].fn(
                                                name="New Doc.odt", type="odt"
                                            )
                                        )
        data = json.loads(result)["data"]
        assert data["id"] == "new-doc"
        assert data["name"] == "New Doc.odt"
        assert data["type"] == "odt"
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_document_invalid_type(self, mcp_docs):
        tools = mcp_docs["tools"]
        result = asyncio.run(tools["docs_create_document"].fn(name="Bad", type="pdf"))
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_create_document_ods(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("new-ods", "Sheet.ods", doc_type="ods", file_size=6000)
        mock_buf = MagicMock()
        mock_buf.read.return_value = b"\x00" * 6000
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.create_document"):
                with patch(f"{DOCS_TEMPLATES}.empty_ods", return_value=mock_buf):
                    with patch(
                        "app.modules.docs.services.doc_meta.inject_metadata",
                        side_effect=lambda data, metadata: data,
                    ):
                        with patch(f"{DOCS_STORAGE}.write_file"):
                            with patch(f"{DOCS_CACHE_DB}.update_file_size"):
                                with patch(
                                    f"{DOCS_CACHE_DB}.get_active_document", return_value=mock_row
                                ):
                                    with patch(f"{UI_EVENTS}.push_ui_event"):
                                        result = asyncio.run(
                                            tools["docs_create_document"].fn(
                                                name="Sheet.ods", type="ods"
                                            )
                                        )
        data = json.loads(result)["data"]
        assert data["type"] == "ods"


class TestDocsMutationTools:
    def test_rename_document(self, mcp_docs):
        tools = mcp_docs["tools"]
        existing_row = _mock_doc_row("doc-1", "Old Name")
        renamed_row = _mock_doc_row("doc-1", "New Name")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(
                f"{DOCS_CACHE_DB}.get_active_document", side_effect=[existing_row, renamed_row]
            ):
                with patch(f"{DOCS_CACHE_DB}.rename_document"):
                    with patch(f"{UI_EVENTS}.push_ui_event"):
                        result = asyncio.run(
                            tools["docs_rename_document"].fn(document_id="doc-1", name="New Name")
                        )
        data = json.loads(result)["data"]
        assert data["name"] == "New Name"

    def test_rename_document_empty_name(self, mcp_docs):
        tools = mcp_docs["tools"]
        result = asyncio.run(tools["docs_rename_document"].fn(document_id="doc-1", name="   "))
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_rename_document_not_found(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=None):
                result = asyncio.run(
                    tools["docs_rename_document"].fn(document_id="missing", name="New")
                )
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_delete_document(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("doc-1", "To Delete")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=mock_row):
                with patch(f"{DOCS_CACHE_DB}.soft_delete_document"):
                    with patch(f"{UI_EVENTS}.push_ui_event"):
                        result = asyncio.run(tools["docs_delete_document"].fn(document_id="doc-1"))
        data = json.loads(result)
        assert "error" not in data

    def test_delete_document_not_found(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=None):
                result = asyncio.run(tools["docs_delete_document"].fn(document_id="missing"))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_upload_document_not_supported(self, mcp_docs):
        tools = mcp_docs["tools"]
        result = asyncio.run(tools["docs_upload_document"].fn())
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_SUPPORTED"


class TestDocsConvertTool:
    def test_convert_pdf_targets_odg(self, mcp_docs):
        tools = mcp_docs["tools"]
        # Source PDF stored with legacy doc_type="odt"; the tool must correct the
        # target to odg via target_odf_type (old code passed odt -> savefailed).
        source_row = _mock_doc_row("src-pdf-1", "Contract", doc_type="odt", original_format="pdf")
        new_row = _mock_doc_row("new-odg-1", "Contract", doc_type="odg", original_format=None)
        fake_odg = b"PK\x03\x04odg-body"

        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", side_effect=[source_row, new_row]):
                with patch(f"{DOCS_STORAGE}.read_file", return_value=b"%PDF-1.4 data"):
                    with patch(
                        "app.modules.docs.services.collabora.convert_upload",
                        return_value=io.BytesIO(fake_odg),
                    ) as mock_convert:
                        with patch(f"{DOCS_STORAGE}.write_file"):
                            with patch(f"{DOCS_CACHE_DB}.create_document"):
                                with patch(f"{DOCS_CACHE_DB}.update_file_size"):
                                    with patch(f"{UI_EVENTS}.push_ui_event"):
                                        result = asyncio.run(
                                            tools["docs_convert_document"].fn(
                                                document_id="src-pdf-1"
                                            )
                                        )

        data = json.loads(result)["data"]
        assert data["type"] == "odg"
        # Regression guard: convert_upload MUST be asked for target "odg".
        assert mock_convert.call_args.args[2] == "odg"

    def test_convert_already_editable_returns_error(self, mcp_docs):
        tools = mcp_docs["tools"]
        native_row = _mock_doc_row("src-1", "Native", doc_type="odt", original_format=None)
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=native_row):
                result = asyncio.run(tools["docs_convert_document"].fn(document_id="src-1"))
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_convert_not_found(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=None):
                result = asyncio.run(tools["docs_convert_document"].fn(document_id="missing"))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_convert_failure_returns_conversion_error(self, mcp_docs):
        from app.modules.docs.services.collabora import ConversionError

        tools = mcp_docs["tools"]
        source_row = _mock_doc_row("src-pdf-2", "Contract", doc_type="odt", original_format="pdf")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_active_document", return_value=source_row):
                with patch(f"{DOCS_STORAGE}.read_file", return_value=b"%PDF-1.4 data"):
                    with patch(
                        "app.modules.docs.services.collabora.convert_upload",
                        side_effect=ConversionError("savefailed"),
                    ):
                        result = asyncio.run(
                            tools["docs_convert_document"].fn(document_id="src-pdf-2")
                        )
        data = json.loads(result)
        assert data["error"]["code"] == "CONVERSION_ERROR"


class TestDocsResponseShape:
    def test_doc_to_dict_has_all_fields(self, mcp_docs):
        from app.mcp.tools.docs import _doc_to_dict

        keys = [
            "id",
            "name",
            "doc_type",
            "original_format",
            "file_size",
            "account_id",
            "created_at",
            "updated_at",
            "deleted_at",
        ]
        vals = {
            "id": "doc-1",
            "name": "Test",
            "doc_type": "odt",
            "original_format": None,
            "file_size": 100,
            "account_id": 1,
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-02T00:00:00",
            "deleted_at": None,
        }
        r = MagicMock()
        r.__getitem__ = lambda self, k: vals[k]
        r.keys = lambda: keys
        result = _doc_to_dict(r)
        assert result["id"] == "doc-1"
        assert result["name"] == "Test"
        assert result["type"] == "odt"
        assert result["size"] == 100
        assert result["created_at"] == "2025-01-01T00:00:00"
        assert result["updated_at"] == "2025-01-02T00:00:00"


class TestDocsTags:
    def test_get_tags(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("doc-1", "Report")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_document", return_value=mock_row):
                with patch(f"{DOCS_CACHE_DB}.get_document_tags", return_value=["urgent"]):
                    result = asyncio.run(tools["docs_get_tags"].fn(document_id="doc-1"))
        data = json.loads(result)["data"]
        assert data["tags"] == ["urgent"]

    def test_get_tags_not_found(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_document", return_value=None):
                result = asyncio.run(tools["docs_get_tags"].fn(document_id="missing"))
        data = json.loads(result)
        assert data["error"]["code"] == "NOT_FOUND"

    def test_update_tags_add_remove(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("doc-1", "Report")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_document", return_value=mock_row):
                with patch(f"{DOCS_CACHE_DB}.update_document_tags") as mock_update:
                    with patch(f"{DOCS_CACHE_DB}.get_document_tags", return_value=["finance"]):
                        with patch("app.modules.docs.services.resync.inject_metadata_from_doc_row"):
                            with patch(f"{UI_EVENTS}.push_ui_event"):
                                result = asyncio.run(
                                    tools["docs_update_tags"].fn(
                                        document_id="doc-1", add=["finance"]
                                    )
                                )
        data = json.loads(result)["data"]
        assert data["tags"] == ["finance"]
        mock_update.assert_called_once()

    def test_update_tags_set_replaces_all(self, mcp_docs):
        tools = mcp_docs["tools"]
        mock_row = _mock_doc_row("doc-1", "Report")
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.get_document", return_value=mock_row):
                with patch(f"{DOCS_CACHE_DB}.set_document_tags") as mock_set:
                    with patch(f"{DOCS_CACHE_DB}.update_document_tags") as mock_update:
                        with patch(f"{DOCS_CACHE_DB}.get_document_tags", return_value=["new"]):
                            with patch(
                                "app.modules.docs.services.resync.inject_metadata_from_doc_row"
                            ):
                                with patch(f"{UI_EVENTS}.push_ui_event"):
                                    result = asyncio.run(
                                        tools["docs_update_tags"].fn(
                                            document_id="doc-1", set=["new"]
                                        )
                                    )
        data = json.loads(result)["data"]
        assert data["tags"] == ["new"]
        mock_set.assert_called_once()
        mock_update.assert_not_called()

    def test_list_tags(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.list_all_tags", return_value=["apple", "zebra"]):
                result = asyncio.run(tools["docs_list_tags"].fn())
        data = json.loads(result)["data"]
        assert data == ["apple", "zebra"]

    def test_list_tags_empty(self, mcp_docs):
        tools = mcp_docs["tools"]
        with patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()):
            with patch(f"{DOCS_CACHE_DB}.list_all_tags", return_value=[]):
                result = asyncio.run(tools["docs_list_tags"].fn())
        data = json.loads(result)["data"]
        assert data == []


class TestDocsFolders:
    def test_create_folder_envelope_has_count(self, mcp_docs):
        # Parity with REST: create_folder response must include `count`.
        tools = mcp_docs["tools"]
        with (
            patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()),
            patch("app.modules.docs.services.folders.normalize_path", return_value="Work"),
            patch("app.modules.docs.services.folders.assert_depth"),
            patch("app.modules.docs.services.folders.ensure_folder_path"),
            patch(
                "app.modules.docs.services.folders.list_flat",
                return_value=[{"path": "Work", "name": "Work", "parent": "", "count": 0}],
            ),
            patch("app.modules.docs.services.folders.leaf_name", return_value="Work"),
        ):
            result = asyncio.run(tools["docs_create_folder"].fn(name="Work"))
        data = json.loads(result)["data"]
        assert data["path"] == "Work"
        assert "count" in data
        assert data["count"] == 0

    def test_rename_folder_envelope_has_parent_and_count(self, mcp_docs):
        # Parity with REST: rename_folder response must include `parent` and `count`.
        tools = mcp_docs["tools"]
        with (
            patch(f"{DOCS}._get_cache_conn", return_value=_mock_conn()),
            patch("app.modules.docs.services.folders.validate_folder_name", return_value="Kid"),
            patch("app.modules.docs.services.folders.normalize_path", return_value="Work/Kid"),
            patch("app.modules.docs.services.folders.parent_path", return_value="Work"),
            patch(f"{DOCS_CACHE_DB}.rename_folder_subtree"),
            patch(f"{DOCS_CACHE_DB}.subtree_documents", return_value=[]),
        ):
            result = asyncio.run(tools["docs_rename_folder"].fn(path="Work/Child", name="Kid"))
        data = json.loads(result)["data"]
        assert data["path"] == "Work/Kid"
        assert data["name"] == "Kid"
        assert data["parent"] == "Work"
        assert data["count"] == 0
