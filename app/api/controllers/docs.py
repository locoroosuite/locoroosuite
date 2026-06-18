import io
import logging
import uuid
from flask import request, g, send_file

from app.api.openapi import create_api_blueprint
from app.api.schemas.common import ErrorResponse
from app.api.schemas.docs import (
    DocumentListResponse, DocumentDetailResponse, ContentResponse,
    DocPath, DraftPath, ListDocumentsQuery, CreateDocumentBody,
    RenameDocumentBody, ReadContentQuery, UpdateContentJsonBody,
    CreateDraftBody, DraftListResponse, ConvertResponse,
)

bp = create_api_blueprint("docs", "Document management")

logger = logging.getLogger(__name__)
from app.api.controllers.helpers import (
    api_response, api_paginated, api_error, require_api_token, require_scope,
    get_api_account_id, ApiError,
)
from app.shared.models.core import CustomerAccount
from app.shared.pandoc_formats import target_odf_type
from app.modules.docs.services.cache import get_cache_path
from app.modules.docs.services.cache_db import (
    open_cache, create_document, get_active_document, list_documents,
    rename_document as db_rename, soft_delete_document, list_trash,
)
from app.modules.docs.services.storage import write_file, read_file, file_exists, _storage_path
from app.shared.ui_events import push_ui_event


def _row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _get_cache_conn(account_id, dek):
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        raise ApiError("NOT_FOUND", "Account not found", 404)
    path = get_cache_path(account)
    return open_cache(path, dek)


def _doc_to_dict(row):
    d = _row_to_dict(row)
    return {
        "id": d["id"],
        "name": d.get("name", ""),
        "type": d.get("doc_type", ""),
        "size": d.get("file_size", 0),
        "created_at": d.get("created_at", ""),
        "updated_at": d.get("updated_at", ""),
    }


def _serialize_document(conn, doc_id):
    row = get_active_document(conn, doc_id)
    if not row:
        from app.modules.docs.services.cache_db import get_document
        row = get_document(conn, doc_id)
    return _doc_to_dict(row) if row else None


@bp.get("/docs/documents", summary="List documents", description="Returns all active documents for the authenticated account. Requires `docs:read` scope.", responses={"200": DocumentListResponse, "401": ErrorResponse})
@require_api_token(scopes=["docs:read"])
@require_scope("docs", "read")
def api_list_documents(query: ListDocumentsQuery):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    limit = min(query.max_results, 200)
    conn = _get_cache_conn(account_id, dek)
    try:
        rows = list_documents(conn, account_id)
        items = [_doc_to_dict(r) for r in rows[:limit]]
        has_more = len(rows) > limit
        return api_paginated(items, has_more=has_more)
    finally:
        conn.close()


@bp.get("/docs/documents/<doc_id>", summary="Get document detail", description="Returns metadata for a single active document by UUID. Requires `docs:read` scope.", responses={"200": DocumentDetailResponse, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:read"])
@require_scope("docs", "read")
def api_get_active_document(path: DocPath):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        return api_response(_doc_to_dict(row))
    finally:
        conn.close()


@bp.post("/docs/documents", summary="Create document", description="Creates a new empty document from a template (odt, ods, or odp). Requires `docs:write` scope.", responses={"201": DocumentDetailResponse, "400": ErrorResponse, "401": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_create_document(body: CreateDocumentBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    name = body.name
    doc_type = body.type
    if doc_type not in ("odt", "ods", "odp"):
        return api_error("VALIDATION_ERROR", "Type must be odt, ods, or odp", 400)
    doc_id = str(uuid.uuid4())
    conn = _get_cache_conn(account_id, dek)
    try:
        create_document(conn, doc_id, name, doc_type, account_id)
    finally:
        conn.close()
    account = CustomerAccount.query.filter_by(id=account_id).first()
    doc_path = _storage_path(account.customer_id, account_id, doc_id)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    from app.modules.docs.services.templates import empty_odt, empty_ods, empty_odp
    template_fn = {"odt": empty_odt, "ods": empty_ods, "odp": empty_odp}.get(doc_type, empty_odt)
    template_data = template_fn()
    doc_path.write_bytes(template_data.read())
    push_ui_event(g.api_context["customer_id"], "docs", "document_created", {"account_id": account_id, "doc_id": doc_id})
    conn = _get_cache_conn(account_id, dek)
    try:
        result = _serialize_document(conn, doc_id)
    finally:
        conn.close()
    return api_response(result or {"id": doc_id, "name": name, "type": doc_type, "size": 0}, 201)


@bp.delete("/docs/documents/<doc_id>", summary="Delete document", description="Soft-deletes a document by UUID. The document is moved to trash and can be restored. Requires `docs:write` scope.", responses={"204": None, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_delete_document(path: DocPath):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        soft_delete_document(conn, doc_id)
        push_ui_event(g.api_context["customer_id"], "docs", "document_deleted", {"account_id": account_id, "doc_id": doc_id})
        return api_response(None, 204)
    finally:
        conn.close()


@bp.put("/docs/documents/<doc_id>", summary="Rename document", description="Renames a document. The new name must be non-empty. Requires `docs:write` scope.", responses={"200": DocumentDetailResponse, "400": ErrorResponse, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_rename_document(path: DocPath, body: RenameDocumentBody):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    name = body.name.strip()
    if not name:
        return api_error("VALIDATION_ERROR", "Name is required", 400)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        db_rename(conn, doc_id, name)
        push_ui_event(g.api_context["customer_id"], "docs", "document_renamed", {"account_id": account_id, "doc_id": doc_id})
        result = _serialize_document(conn, doc_id)
        return api_response(result or {"id": doc_id, "name": name})
    finally:
        conn.close()


@bp.get("/docs/documents/<doc_id>/download", summary="Download document", description="Downloads the document file as a binary attachment. Requires `docs:read` scope.", responses={"200": None, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:read"])
@require_scope("docs", "read")
def api_download_document(path: DocPath):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    account = CustomerAccount.query.filter_by(id=account_id).first()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        d = _row_to_dict(row)
    finally:
        conn.close()
    file_path = _storage_path(account.customer_id, account_id, doc_id)
    if not file_path.exists():
        return api_error("NOT_FOUND", "Document file not found", 404)
    ext = d.get("doc_type", "odt")
    return send_file(str(file_path), as_attachment=True, download_name=f"{d['name']}.{ext}")


@bp.get("/docs/documents/<doc_id>/content", summary="Read document content", description="Extracts and returns the text content of a document. Supports text and markdown output formats. Requires `docs:read` scope.", responses={"200": ContentResponse, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:read"])
@require_scope("docs", "read")
def api_read_content(path: DocPath, query: ReadContentQuery):
    doc_id = path.doc_id
    fmt = query.format
    account_id = get_api_account_id()
    account = CustomerAccount.query.filter_by(id=account_id).first()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        d = _row_to_dict(row)
    finally:
        conn.close()
    file_path = _storage_path(account.customer_id, account_id, doc_id)
    if not file_path.exists():
        return api_response({"content": "", "format": fmt})
    doc_type = d.get("doc_type", "odt")
    if doc_type == "odt":
        content = _extract_odt_text(file_path)
    elif doc_type == "ods":
        content = _extract_ods_text(file_path)
    else:
        content = file_path.read_text(errors="replace")
    return api_response({"content": content, "format": fmt})


def _extract_odt_text(path):
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            with z.open("content.xml") as f:
                import xml.etree.ElementTree as ET
                tree = ET.parse(f)
                root = tree.getroot()
                texts = []
                for elem in root.iter():
                    if elem.text:
                        texts.append(elem.text)
                    if elem.tail:
                        texts.append(elem.tail)
                return " ".join(texts).strip()
    except Exception:
        return ""


def _extract_ods_text(path):
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            with z.open("content.xml") as f:
                import xml.etree.ElementTree as ET
                tree = ET.parse(f)
                root = tree.getroot()
                texts = []
                for elem in root.iter():
                    if elem.text:
                        texts.append(elem.text)
                return " ".join(texts).strip()
    except Exception:
        return ""


@bp.post("/docs/documents/upload", summary="Upload document", description="Uploads a file as a new document. Supports ODF, Office, PDF, and pandoc-compatible formats. Non-ODF files are converted to ODF automatically. Requires `docs:write` scope.", responses={"201": DocumentDetailResponse, "400": ErrorResponse, "401": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_upload_document():
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    if "file" not in request.files:
        return api_error("VALIDATION_ERROR", "No file provided", 400)
    f = request.files["file"]
    if not f.filename:
        return api_error("VALIDATION_ERROR", "Filename is required", 400)
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    allowed_exts = {"odt", "ods", "odp", "docx", "xlsx", "pptx", "pdf", "rtf", "epub", "html", "htm", "tex", "latex", "md", "markdown", "txt", "org", "rst", "docbook", "opml", "csv", "tsv", "ipynb"}
    if ext not in allowed_exts:
        return api_error("VALIDATION_ERROR", f"Unsupported file type: {ext}", 400)
    doc_id = str(uuid.uuid4())
    target_type = {"docx": "odt", "xlsx": "ods", "pptx": "odp"}.get(ext, ext)
    original_format = ext if ext not in ("odt", "ods", "odp") else None
    pandoc_exts = {"rtf", "epub", "html", "htm", "tex", "latex", "md", "markdown", "txt", "org", "rst", "docbook", "opml"}
    if ext in ("odt", "ods", "odp"):
        file_data = f.read()
    elif ext in pandoc_exts:
        raw_data = f.read()
        from app.shared.pandoc_formats import convert_to_odf as pandoc_convert, PANDOC_EXTENSIONS
        pandoc_reader = PANDOC_EXTENSIONS.get(ext, {}).get("pandoc_reader", "plain")
        converted = pandoc_convert(raw_data, pandoc_reader, "odt")
        if not converted:
            return api_error("CONVERSION_ERROR", f"Could not convert .{ext} file with pandoc", 500)
        file_data = converted
        original_format = None
    else:
        file_data = f.read()
    name = f.filename.rsplit(".", 1)[0] if "." in f.filename else f.filename
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.docs.services.cache_db import create_document, update_file_size
        create_document(conn, doc_id, name, target_type, account_id, file_size=0, original_format=original_format)
    finally:
        conn.close()
    size = write_file(account.customer_id, account_id, doc_id, file_data)
    if original_format:
        from app.modules.docs.services.resync import build_doc_metadata
        from app.modules.docs.services.storage import write_sidecar as _write_sidecar
        metadata = build_doc_metadata(doc_id, name, target_type, account_id, original_format=original_format)
        _write_sidecar(account.customer_id, account_id, doc_id, metadata)
    conn = _get_cache_conn(account_id, dek)
    try:
        update_file_size(conn, doc_id, size)
        result = _serialize_document(conn, doc_id)
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "docs", "document_uploaded", {"account_id": account_id, "doc_id": doc_id})
    return api_response(result or {"id": doc_id, "name": name, "type": target_type, "size": size, "original_format": original_format}, 201)


@bp.put("/docs/documents/<doc_id>/content", summary="Update document content", description="Replaces the content of a document. Accepts multipart file upload (ODF) or JSON with markdown/text content. Requires `docs:write` scope.", responses={"200": DocumentDetailResponse, "400": ErrorResponse, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_update_content(path: DocPath):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        d = _row_to_dict(row)
        doc_type = d.get("doc_type", "odt")
    finally:
        conn.close()
    if request.content_type and "multipart/form-data" in request.content_type:
        f = request.files.get("file")
        if not f:
            return api_error("VALIDATION_ERROR", "No file provided", 400)
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext in ("odt", "ods", "odp"):
            file_data = f.read()
        else:
            from app.modules.docs.services.collabora import convert_upload, ConversionError
            try:
                converted = convert_upload(f, f.filename, doc_type)
                file_data = converted.read()
            except (ConversionError, Exception) as exc:
                if not isinstance(exc, ConversionError):
                    logger.exception("Unexpected error during conversion of %s", f.filename)
                return api_error("CONVERSION_ERROR", f"Could not convert {f.filename}: {exc}", 500)
    else:
        data = request.get_json(force=True)
        content = data.get("content", "")
        fmt = data.get("format", "markdown")
        if fmt == "markdown" and content:
            file_data = _markdown_to_odf(content, doc_type)
        else:
            file_data = content.encode("utf-8")
    size = write_file(account.customer_id, account_id, doc_id, file_data)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.docs.services.cache_db import update_file_size
        update_file_size(conn, doc_id, size)
        result = _serialize_document(conn, doc_id)
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "docs", "content_updated", {"account_id": account_id, "doc_id": doc_id})
    return api_response(result or {"id": doc_id, "size": size})


def _markdown_to_odf(markdown_text, doc_type):
    import subprocess
    if doc_type == "odt":
        result = subprocess.run(
            ["pandoc", "-f", "markdown", "-t", "odt"],
            input=markdown_text.encode("utf-8"),
            capture_output=True,
            check=True,
            timeout=30,
        )
        return result.stdout
    import io
    import markdown as md_lib
    html = md_lib.markdown(markdown_text, extensions=["extra"], output_format="html")
    full_html = (
        "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>\n"
        f"<style>{_MARKDOWN_CSS}</style>\n"
        f"</head><body>{html}</body></html>"
    )
    from app.modules.docs.services.collabora import convert_upload
    return convert_upload(io.BytesIO(full_html.encode("utf-8")), "source.html", doc_type).read()


_MARKDOWN_CSS = (
    "@page { margin: 2.54cm; }"
    "body { font-family: 'Liberation Serif', serif; font-size: 12pt; }"
    "p { margin-bottom: 0.35cm; }"
)


@bp.get("/docs/documents/<doc_id>/download/pdf", summary="Export document as PDF", description="Converts a document to PDF and returns it as a download. Requires `docs:read` scope.", responses={"200": None, "401": ErrorResponse, "404": ErrorResponse, "502": ErrorResponse})
@require_api_token(scopes=["docs:read"])
@require_scope("docs", "read")
def api_export_pdf(path: DocPath):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        d = _row_to_dict(row)
    finally:
        conn.close()
    file_data = read_file(account.customer_id, account_id, doc_id)
    if not file_data:
        return api_error("NOT_FOUND", "Document file not found", 404)
    import io
    try:
        from app.modules.docs.services.collabora import convert_upload
        pdf = convert_upload(io.BytesIO(file_data), f"doc.{d.get('doc_type', 'odt')}", "pdf")
        pdf_data = pdf.read()
        return send_file(io.BytesIO(pdf_data), as_attachment=True, download_name=f"{d['name']}.pdf", mimetype="application/pdf")
    except Exception as e:
        return api_error("CONVERSION_ERROR", f"PDF conversion failed: {e}", 502)


@bp.post("/docs/documents/<doc_id>/drafts", summary="Create draft", description="Creates an AI draft document from markdown or text content. The draft is stored as a separate document linked to the source. Requires `docs:write` scope.", responses={"201": DocumentDetailResponse, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_create_draft(path: DocPath, body: CreateDraftBody):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        d = _row_to_dict(row)
        doc_type = d.get("doc_type", "odt")
        orig_name = d.get("name", "Untitled")
    finally:
        conn.close()
    content = body.content
    summary = body.summary
    draft_id = str(uuid.uuid4())
    file_data = content.encode("utf-8")
    fmt = body.format
    if fmt == "markdown" and content:
        file_data = _markdown_to_odf(content, doc_type)
    draft_name = f"{orig_name} (AI Draft)"
    size = write_file(account.customer_id, account_id, draft_id, file_data)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.docs.services.cache_db import create_document, update_file_size
        create_document(conn, draft_id, draft_name, doc_type, account_id, file_size=0)
        update_file_size(conn, draft_id, size)
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "docs", "draft_created", {"account_id": account_id, "doc_id": doc_id, "draft_id": draft_id})
    conn2 = _get_cache_conn(account_id, dek)
    try:
        result = _serialize_document(conn2, draft_id)
    finally:
        conn2.close()
    if result:
        result["source_document_id"] = doc_id
        result["summary"] = summary
    else:
        result = {"id": draft_id, "name": draft_name, "source_document_id": doc_id, "summary": summary}
    return api_response(result, 201)


@bp.get("/docs/documents/<doc_id>/drafts", summary="List drafts", description="Returns all AI draft documents for a source document. Drafts are identified by '(AI Draft)' in their name. Requires `docs:read` scope.", responses={"200": DraftListResponse, "401": ErrorResponse})
@require_api_token(scopes=["docs:read"])
@require_scope("docs", "read")
def api_list_drafts(path: DocPath):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        all_docs = list_documents(conn, account_id)
        drafts = []
        for doc in all_docs:
            d = _row_to_dict(doc)
            name = d.get("name", "")
            if "(AI Draft)" in name:
                drafts.append(_doc_to_dict(d))
        return api_response(drafts)
    finally:
        conn.close()


@bp.post("/docs/documents/<doc_id>/drafts/<draft_id>/apply", summary="Apply draft", description="Replaces the source document content with the draft content, then deletes the draft. Requires `docs:write` scope.", responses={"200": DocumentDetailResponse, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_apply_draft(path: DraftPath):
    doc_id = path.doc_id
    draft_id = path.draft_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    draft_data = read_file(account.customer_id, account_id, draft_id)
    if not draft_data:
        return api_error("NOT_FOUND", "Draft not found", 404)
    size = write_file(account.customer_id, account_id, doc_id, draft_data)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.docs.services.cache_db import update_file_size, hard_delete_document
        update_file_size(conn, doc_id, size)
        hard_delete_document(conn, draft_id)
        result = _serialize_document(conn, doc_id)
    finally:
        conn.close()
    from app.modules.docs.services.storage import delete_file
    delete_file(account.customer_id, account_id, draft_id)
    push_ui_event(g.api_context["customer_id"], "docs", "draft_applied", {"account_id": account_id, "doc_id": doc_id, "draft_id": draft_id})
    return api_response(result or {"id": doc_id, "size": size})


@bp.delete("/docs/documents/<doc_id>/drafts/<draft_id>", summary="Discard draft", description="Permanently deletes a draft document and its file. Requires `docs:write` scope.", responses={"204": None, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_discard_draft(path: DraftPath):
    doc_id = path.doc_id
    draft_id = path.draft_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.docs.services.cache_db import hard_delete_document
        hard_delete_document(conn, draft_id)
    finally:
        conn.close()
    from app.modules.docs.services.storage import delete_file
    delete_file(account.customer_id, account_id, draft_id)
    push_ui_event(g.api_context["customer_id"], "docs", "draft_discarded", {"account_id": account_id, "doc_id": doc_id, "draft_id": draft_id})
    return api_response(None, 204)


@bp.post("/docs/documents/<doc_id>/convert", summary="Convert document to editable format", description="Converts a non-editable document (e.g. docx, pdf) to its editable ODF equivalent (odt, ods, odp). Creates a new document; the original is preserved. Requires `docs:write` scope.", responses={"201": ConvertResponse, "400": ErrorResponse, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["docs:write"])
@require_scope("docs", "write")
def api_convert_document(path: DocPath):
    doc_id = path.doc_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.docs.services.cache_db import get_active_document, create_document, update_file_size
        row = get_active_document(conn, doc_id)
        if not row:
            return api_error("NOT_FOUND", "Document not found", 404)
        doc = _row_to_dict(row)
        if not doc.get("original_format"):
            return api_error("VALIDATION_ERROR", "Document is already editable", 400)
    finally:
        conn.close()

    raw_data = read_file(account.customer_id, account_id, doc_id)
    if raw_data is None:
        return api_error("NOT_FOUND", "File not found", 404)

    original_format = doc["original_format"]
    target_type = target_odf_type(original_format) or doc["doc_type"]
    pandoc_exts = {"rtf", "epub", "html", "htm", "tex", "latex", "md", "markdown", "txt", "org", "rst", "docbook", "opml"}

    if original_format in pandoc_exts:
        from app.shared.pandoc_formats import convert_to_odf as pandoc_convert, PANDOC_EXTENSIONS
        pandoc_reader = PANDOC_EXTENSIONS.get(original_format, {}).get("pandoc_reader", "plain")
        converted = pandoc_convert(raw_data, pandoc_reader, target_type)
        if not converted:
            return api_error("CONVERSION_ERROR", f"Could not convert .{original_format} file", 500)
        file_data = converted
    else:
        from app.modules.docs.services.collabora import convert_upload, ConversionError
        try:
            converted = convert_upload(io.BytesIO(raw_data), f"{doc['name']}.{original_format}", target_type)
            file_data = converted.read()
        except (ConversionError, Exception) as exc:
            if not isinstance(exc, ConversionError):
                logger.exception("Unexpected error during conversion of doc_id=%s", doc_id)
            return api_error("CONVERSION_ERROR", f"Conversion failed: {exc}", 500)

    new_doc_id = str(uuid.uuid4())
    from app.modules.docs.services import doc_meta, resync as resync_svc
    conn = _get_cache_conn(account_id, dek)
    try:
        create_document(conn, new_doc_id, doc["name"], target_type, account_id, file_size=0)
    finally:
        conn.close()
    metadata = resync_svc.build_doc_metadata(new_doc_id, doc["name"], target_type, account_id)
    file_data = doc_meta.inject_metadata(file_data, metadata)
    size = write_file(account.customer_id, account_id, new_doc_id, file_data)
    conn = _get_cache_conn(account_id, dek)
    try:
        update_file_size(conn, new_doc_id, size)
        result = _serialize_document(conn, new_doc_id)
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "docs", "document_converted", {"account_id": account_id, "doc_id": new_doc_id, "source_doc_id": doc_id})
    return api_response(result or {"id": new_doc_id, "name": doc["name"], "type": target_type, "size": size}, 201)
