from __future__ import annotations

import io
import logging
import uuid
from typing import Annotated, Any, Literal

from flask import Flask
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from app.mcp.auth import McpAuthError
from app.mcp.errors import resilient_tool
from app.mcp.helpers import binary_response, err, ok, ok_paginated, resolve_read, resolve_write

_AccId = Annotated[int | None, Field(description="Account ID (uses default account if omitted)")]


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _get_cache_conn(account_id, dek, flask_app):
    from app.shared.models.core import CustomerAccount
    from app.shared.db import db
    from app.modules.docs.services.cache import get_cache_path
    from app.modules.docs.services.cache_db import open_cache
    account = db.session.get(CustomerAccount, account_id)
    if not account:
        raise McpAuthError("NOT_FOUND", f"Account {account_id} not found")
    path = get_cache_path(account)
    return open_cache(path, dek)


def _doc_to_dict(row):
    d = _row_to_dict(row) if not isinstance(row, dict) else row
    return {
        "id": d["id"],
        "name": d.get("name", ""),
        "type": d.get("doc_type", "odt"),
        "size": d.get("file_size", 0),
        "created_at": d.get("created_at", ""),
        "updated_at": d.get("updated_at", ""),
    }


def _storage_path(customer_id, account_id, doc_id, flask_app):
    from pathlib import Path
    from app.config import DATA_DIR
    docs_dir = Path(flask_app.config.get("DOCS_DIR", str(DATA_DIR / "docs")))
    return docs_dir / str(customer_id) / str(account_id) / str(doc_id) / "content"


def _extract_odt_text(path):
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(str(path), "r") as zf:
        with zf.open("content.xml") as f:
            tree = ET.parse(f)
    root = tree.getroot()
    texts = []
    for elem in root.iter():
        if elem.text:
            texts.append(elem.text)
    return "\n".join(texts)


def _extract_ods_text(path):
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(str(path), "r") as zf:
        with zf.open("content.xml") as f:
            tree = ET.parse(f)
    root = tree.getroot()
    texts = []
    for elem in root.iter():
        if elem.text:
            texts.append(elem.text)
    return "\n".join(texts)


def _markdown_to_odf(markdown_text, doc_type, flask_app):
    import subprocess
    import tempfile
    _docs_logger = logging.getLogger(__name__)
    ext_map = {"odt": "odt", "ods": "odt", "odp": "odt"}
    target = ext_map.get(doc_type, "odt")
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(markdown_text)
        md_path = f.name
    out_path = md_path.replace(".md", f".{target}")
    try:
        subprocess.run(["pandoc", "-f", "markdown", "-t", target, md_path, "-o", out_path], capture_output=True, timeout=30, check=True)
        with open(out_path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        _docs_logger.error("pandoc not found — document conversion unavailable")
        return None
    except subprocess.TimeoutExpired:
        _docs_logger.error("pandoc timed out during document conversion")
        return None
    except Exception:
        _docs_logger.exception("pandoc conversion failed")
        return None
    finally:
        import os
        for p in (md_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass


def register(mcp: FastMCP, flask_app: Flask) -> None:
    @mcp.tool(
        name="docs_list_documents",
        title="List Documents",
        description="List documents with optional type filter and search. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_list_documents(
        type: Annotated[Literal["odt", "ods", "odp"] | None, Field(description="Filter by document type: 'odt', 'ods', or 'odp'")] = None,
        search: Annotated[str | None, Field(description="Search by document name")] = None,
        max_results: Annotated[int | None, Field(description="Maximum number of documents to return (1–200, default 50)", ge=1, le=200)] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import list_documents
                rows = list_documents(conn, account_id=aid)
                items = [_doc_to_dict(r) for r in rows]
                if type:
                    items = [d for d in items if d["type"] == type]
                if search:
                    items = [d for d in items if search.lower() in d["name"].lower()]
            finally:
                conn.close()
        limit = max_results or 50
        has_more = len(items) > limit
        return ok_paginated(items[:limit], has_more=has_more)

    @mcp.tool(
        name="docs_get_document",
        title="Get Document",
        description="Get metadata for a specific document. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_get_document(
        document_id: Annotated[str, Field(description="ID of the document to retrieve")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document, get_document
                row = get_active_document(conn, document_id)
                if not row:
                    row = get_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
            finally:
                conn.close()
        return ok(_doc_to_dict(row))

    @mcp.tool(
        name="docs_create_document",
        title="Create Document",
        description="Create a new blank document (odt, ods, or odp).",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_create_document(
        name: Annotated[str, Field(description="Document name")],
        type: Annotated[Literal["odt", "ods", "odp"], Field(description="Document type: 'odt', 'ods', or 'odp'")],
        account_id: _AccId = None,
    ) -> str:
        if type not in ("odt", "ods", "odp"):
            return err("VALIDATION_ERROR", "type must be 'odt', 'ods', or 'odp'")
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        doc_id = str(uuid.uuid4())
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import create_document
                create_document(conn, doc_id, name, type, account_id=aid)
            finally:
                conn.close()
            from app.modules.docs.services.templates import empty_odt, empty_ods, empty_odp
            from app.modules.docs.services.storage import write_file
            template_fn = {"odt": empty_odt, "ods": empty_ods, "odp": empty_odp}
            buf = template_fn[type]()
            data = buf.read()
            write_file(account.customer_id, aid, doc_id, data)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import update_file_size
                update_file_size(conn, doc_id, len(data))
            finally:
                conn.close()
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, doc_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "document_created", {"account_id": aid, "doc_id": doc_id})
        return ok(_doc_to_dict(row) if row else {"id": doc_id, "name": name, "type": type, "size": len(data)})

    @mcp.tool(
        name="docs_rename_document",
        title="Rename Document",
        description="Rename an existing document to a new name.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_rename_document(
        document_id: Annotated[str, Field(description="ID of the document to rename")],
        name: Annotated[str, Field(description="New document name")],
        account_id: _AccId = None,
    ) -> str:
        if not name.strip():
            return err("VALIDATION_ERROR", "name cannot be empty")
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document, rename_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                rename_document(conn, document_id, name.strip())
            finally:
                conn.close()
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "document_renamed", {"account_id": aid, "doc_id": document_id})
        return ok(_doc_to_dict(row) if row else {"id": document_id, "name": name.strip()})

    @mcp.tool(
        name="docs_delete_document",
        title="Delete Document",
        description="Soft delete a document (move to trash).",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True),
    )
    @resilient_tool
    async def docs_delete_document(
        document_id: Annotated[str, Field(description="ID of the document to delete")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document, soft_delete_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                soft_delete_document(conn, document_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "document_deleted", {"account_id": aid, "doc_id": document_id})
        return ok()

    @mcp.tool(
        name="docs_download_document",
        title="Download Document",
        description="Download a document file as base64-encoded content. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_download_document(
        document_id: Annotated[str, Field(description="ID of the document to download")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            from app.modules.docs.services.storage import read_file
            file_data = read_file(account.customer_id, aid, document_id)
            if not file_data:
                return err("NOT_FOUND", "Document file not found")
            ext = d.get("doc_type", "odt")
            mime_map = {"odt": "application/vnd.oasis.opendocument.text", "ods": "application/vnd.oasis.opendocument.spreadsheet", "odp": "application/vnd.oasis.opendocument.presentation"}
            return ok(binary_response(file_data, mime_map.get(ext, "application/octet-stream"), f"{d['name']}.{ext}"))

    @mcp.tool(
        name="docs_upload_document",
        title="Upload Document",
        description="Upload a new document file (not supported via MCP — use the web interface or REST API).",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_upload_document(account_id: _AccId = None) -> str:
        return err("NOT_SUPPORTED", "File uploads are not supported via MCP. Use the web interface or API.")

    @mcp.tool(
        name="docs_read_content",
        title="Read Document Content",
        description="Read the text content of a document. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_read_content(
        document_id: Annotated[str, Field(description="ID of the document to read")],
        format: Annotated[Literal["text", "markdown"] | None, Field(description="Output format: 'text' or 'markdown' (default: text)")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        fmt = format or "text"
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            file_path = _storage_path(account.customer_id, aid, document_id, flask_app)
            if not file_path.exists():
                return ok({"content": "", "format": fmt})
            doc_type = d.get("doc_type", "odt")
            if doc_type == "odt":
                content = _extract_odt_text(file_path)
            elif doc_type == "ods":
                content = _extract_ods_text(file_path)
            else:
                content = file_path.read_text(encoding="utf-8", errors="replace")
        return ok({"content": content, "format": fmt})

    @mcp.tool(
        name="docs_update_content",
        title="Update Document Content",
        description="Replace the content of a document. Supports markdown conversion for ODF formats.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_update_content(
        document_id: Annotated[str, Field(description="ID of the document to update")],
        content: Annotated[str, Field(description="New document content in markdown format")],
        format: Annotated[Literal["markdown", "text"] | None, Field(description="Input format: 'markdown' or 'text' (default: markdown)")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        fmt = format or "markdown"
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                d = _row_to_dict(row)
                doc_type = d.get("doc_type", "odt")
            finally:
                conn.close()
            if fmt == "markdown":
                odf_bytes = _markdown_to_odf(content, doc_type, flask_app)
                if not odf_bytes:
                    return err("CONVERSION_ERROR", "Failed to convert markdown to ODF")
                from app.modules.docs.services.storage import write_file
                write_file(account.customer_id, aid, document_id, odf_bytes)
                conn = _get_cache_conn(aid, dek, flask_app)
                try:
                    from app.modules.docs.services.cache_db import update_file_size
                    update_file_size(conn, document_id, len(odf_bytes))
                finally:
                    conn.close()
            else:
                from app.modules.docs.services.storage import write_file
                write_file(account.customer_id, aid, document_id, content.encode("utf-8"))
                conn = _get_cache_conn(aid, dek, flask_app)
                try:
                    from app.modules.docs.services.cache_db import update_file_size
                    update_file_size(conn, document_id, len(content.encode("utf-8")))
                finally:
                    conn.close()
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "document_updated", {"account_id": aid, "doc_id": document_id})
        return ok(_doc_to_dict(row) if row else {"id": document_id})

    @mcp.tool(
        name="docs_create_draft",
        title="Create Document Draft",
        description="Create an AI draft for a document.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_create_draft(
        document_id: Annotated[str, Field(description="ID of the document to create a draft for")],
        content: Annotated[str, Field(description="Draft content in markdown format")],
        summary: Annotated[str | None, Field(description="Brief description of the changes")] = None,
        format: Annotated[Literal["markdown", "text"] | None, Field(description="Input format: 'markdown' or 'text' (default: markdown)")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        fmt = format or "markdown"
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                d = _row_to_dict(row)
                doc_type = d.get("doc_type", "odt")
                orig_name = d.get("name", "Untitled")
            finally:
                conn.close()
            draft_id = str(uuid.uuid4())
            if fmt == "markdown":
                odf_bytes = _markdown_to_odf(content, doc_type, flask_app)
                if not odf_bytes:
                    return err("CONVERSION_ERROR", "Failed to convert markdown to ODF")
            else:
                odf_bytes = content.encode("utf-8")
            from app.modules.docs.services.storage import write_file
            write_file(account.customer_id, aid, draft_id, odf_bytes)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import create_document, update_file_size
                draft_name = f"{orig_name} (AI Draft)"
                create_document(conn, draft_id, draft_name, doc_type, account_id=aid)
                update_file_size(conn, draft_id, len(odf_bytes))
            finally:
                conn.close()
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, draft_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "draft_created", {"account_id": aid, "doc_id": document_id, "draft_id": draft_id})
        return ok(_doc_to_dict(row) if row else {"id": draft_id, "name": draft_name})

    @mcp.tool(
        name="docs_list_drafts",
        title="List Document Drafts",
        description="List AI drafts for a document. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_list_drafts(
        document_id: Annotated[str, Field(description="ID of the document to list drafts for")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import list_documents
                rows = list_documents(conn, account_id=aid)
                drafts = [_doc_to_dict(r) for r in rows if "(AI Draft)" in (r.get("name", "") if isinstance(r, dict) else r["name"])]
            finally:
                conn.close()
        return ok(drafts)

    @mcp.tool(
        name="docs_apply_draft",
        title="Apply Document Draft",
        description="Apply (accept) an AI draft, replacing the document content.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_apply_draft(
        document_id: Annotated[str, Field(description="ID of the document to apply the draft to")],
        draft_id: Annotated[str, Field(description="ID of the draft to apply")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            from app.modules.docs.services.storage import read_file, write_file, delete_file
            file_data = read_file(account.customer_id, aid, draft_id)
            if not file_data:
                return err("NOT_FOUND", "Draft file not found")
            write_file(account.customer_id, aid, document_id, file_data)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import update_file_size, hard_delete_document
                update_file_size(conn, document_id, len(file_data))
                hard_delete_document(conn, draft_id)
            finally:
                conn.close()
            delete_file(account.customer_id, aid, draft_id)
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "draft_applied", {"account_id": aid, "doc_id": document_id, "draft_id": draft_id})
        return ok()

    @mcp.tool(
        name="docs_discard_draft",
        title="Discard Document Draft",
        description="Discard an AI draft.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True),
    )
    @resilient_tool
    async def docs_discard_draft(
        document_id: Annotated[str, Field(description="ID of the document the draft belongs to")],
        draft_id: Annotated[str, Field(description="ID of the draft to discard")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import hard_delete_document
                hard_delete_document(conn, draft_id)
            finally:
                conn.close()
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            from app.modules.docs.services.storage import delete_file
            delete_file(account.customer_id, aid, draft_id)
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "draft_discarded", {"account_id": aid, "doc_id": document_id, "draft_id": draft_id})
        return ok()

    @mcp.tool(
        name="docs_export_pdf",
        title="Export Document as PDF",
        description="Export a document as a PDF file (base64-encoded). Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_export_pdf(
        document_id: Annotated[str, Field(description="ID of the document to export")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            from app.modules.docs.services.storage import read_file
            file_data = read_file(account.customer_id, aid, document_id)
            if not file_data:
                return err("NOT_FOUND", "Document file not found")
            try:
                from app.modules.docs.services.collabora import convert_upload
                pdf = convert_upload(io.BytesIO(file_data), f"doc.{d.get('doc_type', 'odt')}", "pdf")
                pdf_data = pdf.read()
            except Exception as exc:
                return err("CONVERSION_ERROR", f"PDF conversion failed: {exc}")
        return ok(binary_response(pdf_data, "application/pdf", f"{d['name']}.pdf"))

    @mcp.tool(
        name="docs_convert_document",
        title="Convert Document to Editable",
        description="Convert a non-ODF document (PDF, DOCX, etc.) to an editable ODF document. The original is preserved.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_convert_document(
        document_id: Annotated[str, Field(description="ID of the document to convert")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_active_document
                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                d = _row_to_dict(row)
                original_format = d.get("original_format")
                if not original_format:
                    return err("VALIDATION_ERROR", "Document is not a converted type")
            finally:
                conn.close()
            from app.modules.docs.services.storage import read_file
            file_data = read_file(account.customer_id, aid, document_id)
            if not file_data:
                return err("NOT_FOUND", "Document file not found")
            doc_type = d.get("doc_type", "odt")
            try:
                from app.modules.docs.services.collabora import convert_upload
                converted = convert_upload(io.BytesIO(file_data), f"doc.{original_format}", doc_type)
                converted_bytes = converted.read()
            except Exception as exc:
                return err("CONVERSION_ERROR", f"Conversion failed: {exc}")
            new_id = str(uuid.uuid4())
            from app.modules.docs.services.storage import write_file
            write_file(account.customer_id, aid, new_id, converted_bytes)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import create_document, update_file_size
                create_document(conn, new_id, d.get("name", "Untitled"), doc_type, account_id=aid)
                update_file_size(conn, new_id, len(converted_bytes))
                row = get_active_document(conn, new_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "docs", "document_converted", {"account_id": aid, "doc_id": new_id})
        return ok(_doc_to_dict(row) if row else {"id": new_id})
