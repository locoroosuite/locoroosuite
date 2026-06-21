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
from app.modules.docs.services.cache_db import parse_tags

_AccId = Annotated[int | None, Field(description="Account ID (uses default account if omitted)")]


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}  # Row iteration yields values, not keys; .keys() is required


def _get_cache_conn(account_id, dek, flask_app):
    from app.modules.docs.services.cache import get_cache_path
    from app.modules.docs.services.cache_db import open_cache
    from app.shared.db import db
    from app.shared.models.core import CustomerAccount

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
        "folder_path": d.get("folder_path", ""),
        "tags": parse_tags(d.get("tags")),
    }


def _storage_path(customer_id, account_id, doc_id, flask_app):
    from pathlib import Path

    from app.config import DATA_DIR

    docs_dir = Path(flask_app.config.get("DOCS_DIR", str(DATA_DIR / "docs")))
    return docs_dir / str(customer_id) / str(account_id) / str(doc_id) / "content"


def _extract_odt_text(path):
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(str(path), "r") as zf, zf.open("content.xml") as f:
        tree = ET.parse(f)
    root = tree.getroot()
    texts = []
    for elem in root.iter():
        if elem.text:
            texts.append(elem.text)
    return "\n".join(texts)


def _extract_ods_text(path):
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(str(path), "r") as zf, zf.open("content.xml") as f:
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
        subprocess.run(
            ["pandoc", "-f", "markdown", "-t", target, md_path, "-o", out_path],
            capture_output=True,
            timeout=30,
            check=True,
        )
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
        type: Annotated[
            Literal["odt", "ods", "odp"] | None,
            Field(description="Filter by document type: 'odt', 'ods', or 'odp'"),
        ] = None,
        search: Annotated[str | None, Field(description="Search by document name")] = None,
        folder: Annotated[
            str | None,
            Field(
                description="Filter to documents directly in this folder path (exact match, empty string = root)"
            ),
        ] = None,
        tag: Annotated[
            str | None, Field(description="Filter to documents carrying this tag")
        ] = None,
        max_results: Annotated[
            int | None,
            Field(
                description="Maximum number of documents to return (1–200, default 50)",
                ge=1,
                le=200,
            ),
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import list_documents

                folder_filter = folder if folder is not None else None
                tag_filter = tag if tag is not None else None
                rows = list_documents(conn, account_id=aid, folder=folder_filter, tag=tag_filter)
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
        type: Annotated[
            Literal["odt", "ods", "odp"], Field(description="Document type: 'odt', 'ods', or 'odp'")
        ],
        folder: Annotated[
            str | None,
            Field(description="Folder path to create the document in (empty/omitted = root)"),
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        if type not in ("odt", "ods", "odp"):
            return err("VALIDATION_ERROR", "type must be 'odt', 'ods', or 'odp'")
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        folder_path = (folder or "").strip().strip("/")
        if folder_path:
            from app.modules.docs.services import folders as folders_svc

            try:
                for seg in folder_path.split("/"):
                    folders_svc.validate_folder_name(seg)
                folders_svc.assert_depth(folder_path)
            except folders_svc.FolderError as exc:
                return err("VALIDATION_ERROR", str(exc))
        doc_id = str(uuid.uuid4())
        with flask_app.app_context():
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            from app.modules.docs.services import doc_meta
            from app.modules.docs.services import folders as folders_svc
            from app.modules.docs.services import resync as resync_svc

            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import create_document

                if folder_path:
                    folders_svc.ensure_folder_path(conn, aid, folder_path)
                create_document(conn, doc_id, name, type, account_id=aid, folder_path=folder_path)
            finally:
                conn.close()
            from app.modules.docs.services.storage import write_file
            from app.modules.docs.services.templates import empty_odp, empty_ods, empty_odt

            template_fn = {"odt": empty_odt, "ods": empty_ods, "odp": empty_odp}
            data = template_fn[type]().read()
            metadata = resync_svc.build_doc_metadata(
                doc_id, name, type, aid, folder_path=folder_path
            )
            data = doc_meta.inject_metadata(data, metadata)
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

        push_ui_event(
            ctx["customer_id"], "docs", "document_created", {"account_id": aid, "doc_id": doc_id}
        )
        return ok(
            _doc_to_dict(row)
            if row
            else {
                "id": doc_id,
                "name": name,
                "type": type,
                "size": len(data),
                "folder_path": folder_path,
            }
        )

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
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

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

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "document_renamed",
            {"account_id": aid, "doc_id": document_id},
        )
        return ok(_doc_to_dict(row) if row else {"id": document_id, "name": name.strip()})

    @mcp.tool(
        name="docs_delete_document",
        title="Delete Document",
        description="Soft delete a document (move to trash).",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True
        ),
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
                from app.modules.docs.services.cache_db import (
                    get_active_document,
                    soft_delete_document,
                )

                row = get_active_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                soft_delete_document(conn, document_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "document_deleted",
            {"account_id": aid, "doc_id": document_id},
        )
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
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

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
            mime_map = {
                "odt": "application/vnd.oasis.opendocument.text",
                "ods": "application/vnd.oasis.opendocument.spreadsheet",
                "odp": "application/vnd.oasis.opendocument.presentation",
            }
            return ok(
                binary_response(
                    file_data, mime_map.get(ext, "application/octet-stream"), f"{d['name']}.{ext}"
                )
            )

    @mcp.tool(
        name="docs_upload_document",
        title="Upload Document",
        description="Upload a new document file (not supported via MCP — use the web interface or REST API).",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_upload_document(account_id: _AccId = None) -> str:
        return err(
            "NOT_SUPPORTED", "File uploads are not supported via MCP. Use the web interface or API."
        )

    @mcp.tool(
        name="docs_read_content",
        title="Read Document Content",
        description="Read the text content of a document. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_read_content(
        document_id: Annotated[str, Field(description="ID of the document to read")],
        format: Annotated[
            Literal["text", "markdown"] | None,
            Field(description="Output format: 'text' or 'markdown' (default: text)"),
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        fmt = format or "text"
        with flask_app.app_context():
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

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
        format: Annotated[
            Literal["markdown", "text"] | None,
            Field(description="Input format: 'markdown' or 'text' (default: markdown)"),
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        fmt = format or "markdown"
        with flask_app.app_context():
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

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

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "document_updated",
            {"account_id": aid, "doc_id": document_id},
        )
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
        summary: Annotated[
            str | None, Field(description="Brief description of the changes")
        ] = None,
        format: Annotated[
            Literal["markdown", "text"] | None,
            Field(description="Input format: 'markdown' or 'text' (default: markdown)"),
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        fmt = format or "markdown"
        with flask_app.app_context():
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

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

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "draft_created",
            {"account_id": aid, "doc_id": document_id, "draft_id": draft_id},
        )
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
                drafts = [
                    _doc_to_dict(r)
                    for r in rows
                    if "(AI Draft)" in (r.get("name", "") if isinstance(r, dict) else r["name"])
                ]
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
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            from app.modules.docs.services.storage import delete_file, read_file, write_file

            file_data = read_file(account.customer_id, aid, draft_id)
            if not file_data:
                return err("NOT_FOUND", "Draft file not found")
            write_file(account.customer_id, aid, document_id, file_data)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import (
                    hard_delete_document,
                    update_file_size,
                )

                update_file_size(conn, document_id, len(file_data))
                hard_delete_document(conn, draft_id)
            finally:
                conn.close()
            delete_file(account.customer_id, aid, draft_id)
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "draft_applied",
            {"account_id": aid, "doc_id": document_id, "draft_id": draft_id},
        )
        return ok()

    @mcp.tool(
        name="docs_discard_draft",
        title="Discard Document Draft",
        description="Discard an AI draft.",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True
        ),
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
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            from app.modules.docs.services.storage import delete_file

            delete_file(account.customer_id, aid, draft_id)
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "draft_discarded",
            {"account_id": aid, "doc_id": document_id, "draft_id": draft_id},
        )
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
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

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

                pdf = convert_upload(
                    io.BytesIO(file_data), f"doc.{d.get('doc_type', 'odt')}", "pdf"
                )
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
            from app.shared.db import db
            from app.shared.models.core import CustomerAccount

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
            from app.shared.pandoc_formats import target_odf_type

            doc_type = target_odf_type(original_format) or d.get("doc_type", "odt")
            try:
                from app.modules.docs.services.collabora import convert_upload

                converted = convert_upload(
                    io.BytesIO(file_data), f"doc.{original_format}", doc_type
                )
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

        push_ui_event(
            ctx["customer_id"], "docs", "document_converted", {"account_id": aid, "doc_id": new_id}
        )
        return ok(_doc_to_dict(row) if row else {"id": new_id})

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    @mcp.tool(
        name="docs_list_folders",
        title="List Document Folders",
        description="List all folders for the account (explicit rows plus paths inferred from documents). Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_list_folders(
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            from app.modules.docs.services import folders as folders_svc

            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                items = folders_svc.list_flat(conn, aid)
            finally:
                conn.close()
        return ok(items)

    @mcp.tool(
        name="docs_create_folder",
        title="Create Document Folder",
        description="Create a folder (and any missing ancestors). Idempotent: creating an existing path succeeds.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_create_folder(
        name: Annotated[str, Field(description="Folder name (leaf segment)")],
        parent: Annotated[
            str | None, Field(description="Parent folder path (empty/omitted = top-level)")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        from app.modules.docs.services import folders as folders_svc

        try:
            path = folders_svc.normalize_path((parent or "").strip().strip("/"), name)
            folders_svc.assert_depth(path)
        except folders_svc.FolderError as exc:
            return err("VALIDATION_ERROR", str(exc))
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                folders_svc.ensure_folder_path(conn, aid, path)
                items = {f["path"]: f for f in folders_svc.list_flat(conn, aid)}
            finally:
                conn.close()
        parent_path = (parent or "").strip().strip("/")
        item = items.get(
            path,
            {"path": path, "name": folders_svc.leaf_name(path), "parent": parent_path, "count": 0},
        )
        return ok(item)

    @mcp.tool(
        name="docs_rename_folder",
        title="Rename Document Folder",
        description="Rename a folder and its entire subtree. Document folder paths are rewritten accordingly.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_rename_folder(
        path: Annotated[str, Field(description="Existing folder path to rename")],
        name: Annotated[str, Field(description="New leaf folder name")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        from app.modules.docs.services import folders as folders_svc
        from app.modules.docs.services import resync as resync_svc

        old_path = (path or "").strip().strip("/")
        if not old_path:
            return err("VALIDATION_ERROR", "path is required")
        try:
            new_name = folders_svc.validate_folder_name(name)
        except folders_svc.FolderError as exc:
            return err("VALIDATION_ERROR", str(exc))
        new_path = folders_svc.normalize_path(folders_svc.parent_path(old_path), new_name)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import (
                    rename_folder_subtree,
                    subtree_documents,
                )

                rename_folder_subtree(conn, aid, old_path, new_path)
                for d in subtree_documents(conn, aid, new_path):
                    if not d.get("deleted_at"):
                        resync_svc.inject_metadata_from_doc_row(ctx["customer_id"], aid, d)
            finally:
                conn.close()
        return ok(
            {
                "path": new_path,
                "name": new_name,
                "parent": folders_svc.parent_path(new_path),
                "count": 0,
            }
        )

    @mcp.tool(
        name="docs_delete_folder",
        title="Delete Document Folder",
        description="Delete a folder subtree. Contained documents are moved to the deleted folder's parent.",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True
        ),
    )
    @resilient_tool
    async def docs_delete_folder(
        path: Annotated[str, Field(description="Folder path to delete")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        from app.modules.docs.services import folders as folders_svc
        from app.modules.docs.services import resync as resync_svc

        target = (path or "").strip().strip("/")
        if not target:
            return err("VALIDATION_ERROR", "path is required")
        parent = folders_svc.parent_path(target)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import (
                    delete_folder_subtree_rows,
                    get_document,
                    move_subtree_docs_to_parent,
                    subtree_documents,
                )

                moved = [d for d in subtree_documents(conn, aid, target) if not d.get("deleted_at")]
                move_subtree_docs_to_parent(conn, aid, target, parent)
                delete_folder_subtree_rows(conn, aid, target)
                for d in moved:
                    doc = get_document(conn, d["id"])
                    if doc and not doc.get("deleted_at"):
                        resync_svc.inject_metadata_from_doc_row(ctx["customer_id"], aid, doc)
            finally:
                conn.close()
        return ok({"path": target, "moved_to": parent})

    @mcp.tool(
        name="docs_move_document",
        title="Move Document",
        description="Move a document to a folder (empty/omitted folder = root).",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_move_document(
        document_id: Annotated[str, Field(description="ID of the document to move")],
        folder: Annotated[
            str | None, Field(description="Target folder path (empty/omitted = root)")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        from app.modules.docs.services import folders as folders_svc
        from app.modules.docs.services import resync as resync_svc

        target = (folder or "").strip().strip("/")
        if target:
            try:
                for seg in target.split("/"):
                    folders_svc.validate_folder_name(seg)
                folders_svc.assert_depth(target)
            except folders_svc.FolderError as exc:
                return err("VALIDATION_ERROR", str(exc))
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_document, set_document_folder

                row = get_document(conn, document_id)
                if not row or row.get("deleted_at"):
                    return err("NOT_FOUND", "Document not found")
                if target:
                    folders_svc.ensure_folder_path(conn, aid, target)
                set_document_folder(conn, document_id, target)
                resync_svc.inject_metadata_from_doc_row(
                    ctx["customer_id"], aid, get_document(conn, document_id)
                )
                row = get_document(conn, document_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "document_moved",
            {"account_id": aid, "doc_id": document_id, "folder": target},
        )
        return ok(_doc_to_dict(row) if row else {"id": document_id, "folder_path": target})

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    @mcp.tool(
        name="docs_get_tags",
        title="Get Document Tags",
        description="Get the tags applied to a document. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_get_tags(
        document_id: Annotated[str, Field(description="ID of the document")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import get_document, get_document_tags

                row = get_document(conn, document_id)
                if not row:
                    return err("NOT_FOUND", "Document not found")
                tags = get_document_tags(conn, document_id)
            finally:
                conn.close()
        return ok({"tags": tags})

    @mcp.tool(
        name="docs_update_tags",
        title="Update Document Tags",
        description="Add and/or remove tags on a document, or replace the full tag list with `set`. Each tag is max 50 chars.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_update_tags(
        document_id: Annotated[str, Field(description="ID of the document to tag")],
        add: Annotated[list[str] | None, Field(description="Tags to add")] = None,
        remove: Annotated[list[str] | None, Field(description="Tags to remove")] = None,
        set: Annotated[
            list[str] | None,
            Field(
                description="Replace the full tag list with this list (takes precedence over add/remove)"
            ),
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "docs", account_id)
        with flask_app.app_context():
            from app.modules.docs.services import resync as resync_svc

            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import (
                    get_document,
                    get_document_tags,
                    set_document_tags,
                    update_document_tags,
                )

                row = get_document(conn, document_id)
                if not row or row.get("deleted_at"):
                    return err("NOT_FOUND", "Document not found")
                if set is not None:
                    set_document_tags(conn, document_id, set)
                else:
                    update_document_tags(conn, document_id, add=add or [], remove=remove or [])
                resync_svc.inject_metadata_from_doc_row(
                    ctx["customer_id"], aid, get_document(conn, document_id)
                )
                tags = get_document_tags(conn, document_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "docs",
            "document_tagged",
            {"account_id": aid, "doc_id": document_id},
        )
        return ok({"tags": tags})

    @mcp.tool(
        name="docs_list_tags",
        title="List Account Tags",
        description="List the distinct tags in use across the account's active documents (sorted, case-insensitive). Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def docs_list_tags(
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "docs", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.docs.services.cache_db import list_all_tags

                tags = list_all_tags(conn, aid)
            finally:
                conn.close()
        return ok(tags)
