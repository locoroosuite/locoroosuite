import io
import logging
import uuid
from urllib.parse import quote

from flask import request, redirect, url_for, session, render_template, send_file, jsonify, current_app

from app.shared.auth import require_customer
from app.shared.models.core import CustomerAccount
from app.shared.pandoc_formats import target_odf_type
from app.modules.docs.controllers.helpers import docs_bp, _get_account, _open_cache_for_account
from app.modules.docs.services import cache_db, storage, wopi_token, collabora, sharing
from app.modules.docs.services import doc_meta, resync as resync_svc
from app.modules.docs.services import folders as folders_svc
from app.modules.docs.services.templates import empty_odt, empty_ods, empty_odp, TYPE_NAMES, MIME_TYPES

logger = logging.getLogger(__name__)

ALLOWED_UPLOAD_EXTENSIONS = {"odt", "ods", "odp", "docx", "xlsx", "pptx", "pdf"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

PANDOC_UPLOAD_EXTENSIONS = {
    "rtf", "epub", "html", "htm", "tex", "latex",
    "md", "markdown", "txt", "org", "rst", "docbook", "opml",
    "csv", "tsv", "ipynb",
}

ALL_UPLOAD_EXTENSIONS = ALLOWED_UPLOAD_EXTENSIONS | PANDOC_UPLOAD_EXTENSIONS


@docs_bp.route("/docs/")
@require_customer
def index():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    current_folder = (request.args.get("folder") or "").strip().strip("/")
    current_tag = (request.args.get("tag") or "").strip()
    section = request.args.get("section", "my")

    def _snapshot():
        docs = cache_db.list_documents(
            conn, account_id,
            folder=current_folder or None,
            tag=current_tag or None,
        )
        for d in docs:
            d["tag_list"] = cache_db.parse_tags(d.get("tags"))
        return (
            docs,
            cache_db.list_trash(conn, account_id),
            folders_svc.list_tree(conn, account_id),
            cache_db.list_all_tags(conn, account_id),
        )

    try:
        documents, trash, folder_tree, all_tags = _snapshot()

        if request.args.get("resync") == "1":
            try:
                resync_svc.resync_docs(conn, user_id, account_id)
                documents, trash, folder_tree, all_tags = _snapshot()
            except Exception:
                logger.exception("Auto-resync failed for user=%s account=%s", user_id, account_id)
    finally:
        conn.close()

    user_emails = _get_user_emails(user_id)
    shared_docs = sharing.get_shared_with_user(user_emails)

    owner_ids = {s.owner_account_id for s in shared_docs}
    owner_map = {}
    if owner_ids:
        for acc in CustomerAccount.query.filter(CustomerAccount.id.in_(owner_ids)):
            owner_map[acc.id] = acc.email_address
    for s in shared_docs:
        s.owner_email = owner_map.get(s.owner_account_id, "")

    breadcrumbs = _folder_breadcrumbs(current_folder)

    return render_template(
        "docs_list.html",
        documents=documents,
        trash=trash,
        shared_docs=shared_docs,
        account=account,
        section=section,
        folder_tree=folder_tree,
        all_tags=all_tags,
        current_folder=current_folder,
        current_tag=current_tag,
        breadcrumbs=breadcrumbs,
    )


def _folder_breadcrumbs(folder_path):
    folder_path = (folder_path or "").strip("/")
    if not folder_path:
        return []
    parts = folder_path.split("/")
    crumbs = []
    cumulative = ""
    for part in parts:
        cumulative = part if not cumulative else cumulative + "/" + part
        crumbs.append({"name": part, "path": cumulative})
    return crumbs


def _resolve_account_id():
    """Resolve account_id from query param (?account_id=) falling back to session.

    Allows other modules' UIs (e.g. mail compose "attach from docs") to target a
    specific account without relying on the docs sidebar's active account.
    """
    raw = request.args.get("account_id") or ""
    if raw.isdigit():
        return int(raw)
    return session.get("active_account_id")


@docs_bp.route("/docs/api/list")
@require_customer
def api_list():
    user_id = session.get("user_id")
    account_id = _resolve_account_id()
    if not account_id:
        return jsonify({"error": {"code": "no_account", "message": "No account selected."}}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "no_cache", "message": "Unable to open document store."}}), 503

    try:
        documents = cache_db.list_documents(conn, account_id)
    finally:
        conn.close()

    q = (request.args.get("q") or "").strip().lower()
    items = []
    for doc in documents:
        name = doc.get("name", "")
        if q and q not in (name or "").lower():
            continue
        ext = doc.get("original_format") or doc.get("doc_type") or "odt"
        items.append({
            "id": doc.get("id"),
            "name": name,
            "doc_type": doc.get("doc_type"),
            "original_format": doc.get("original_format"),
            "ext": ext,
            "file_size": doc.get("file_size", 0),
            "updated_at": doc.get("updated_at"),
        })
    return jsonify({"documents": items, "account_id": account_id})


@docs_bp.route("/docs/new", methods=["POST"])
@require_customer
def create():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    doc_type = request.form.get("doc_type", "odt")
    if doc_type not in ("odt", "ods", "odp"):
        doc_type = "odt"

    folder = (request.form.get("folder") or "").strip().strip("/")
    if folder:
        try:
            for seg in folder.split("/"):
                folders_svc.validate_folder_name(seg)
            folders_svc.assert_depth(folder)
        except folders_svc.FolderError:
            folder = ""

    doc_id = uuid.uuid4().hex
    name = TYPE_NAMES.get(doc_type, "Untitled Document")

    template_fn = {"odt": empty_odt, "ods": empty_ods, "odp": empty_odp}.get(doc_type, empty_odt)
    template_buf = template_fn()
    template_data = template_buf.read()

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        if folder:
            folders_svc.ensure_folder_path(conn, account_id, folder)
        cache_db.create_document(conn, doc_id, name, doc_type, account_id, file_size=0, folder_path=folder)
        metadata = resync_svc.build_doc_metadata(doc_id, name, doc_type, account_id, folder_path=folder)
        template_data = doc_meta.inject_metadata(template_data, metadata)
        storage.write_file(user_id, account_id, doc_id, template_data)
        cache_db.update_file_size(conn, doc_id, len(template_data))
    finally:
        conn.close()

    return redirect(url_for("docs.editor", doc_id=doc_id))


@docs_bp.route("/docs/<doc_id>/edit")
@require_customer
def editor(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc or doc.get("deleted_at"):
            return redirect(url_for("docs.index"))

        token = wopi_token.generate_token(doc_id, user_id, account_id, writable=True)

        collabora_internal = (
            current_app.config.get("COLLABORA_INTERNAL_URL")
            or current_app.config.get("COLLABORA_URL", "http://localhost:9980")
        )
        collabora_public = (
            current_app.config.get("COLLABORA_PUBLIC_URL")
            or collabora_internal
        )

        wopi_host_url = current_app.config.get("WOPI_HOST_URL", "") or request.host_url.rstrip("/")
        wopi_src = wopi_host_url + url_for("docs.wopi_check_file_info", doc_id=doc_id)
        edit_base = collabora.get_edit_url(doc["doc_type"], collabora_internal) or f"{collabora_public}/browser/dist/cool.html?"

        edit_base_http = edit_base.replace("https://", "http://", 1)
        if edit_base_http.startswith(collabora_internal):
            edit_base = collabora_public + edit_base_http[len(collabora_internal):]

        collabora_src = (
            f"{edit_base}"
            f"WOPISrc={quote(wopi_src, safe='')}"
            f"&access_token={quote(token, safe='')}"
        )

        return render_template(
            "docs_editor.html",
            doc=doc,
            account=account,
            collabora_src=collabora_src,
            token=token,
        )
    finally:
        conn.close()


@docs_bp.route("/docs/<doc_id>/rename", methods=["POST"])
@require_customer
def rename(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"error": "no account"}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": "unauthorized"}), 401

    name = request.form.get("name", "").strip()
    if not name or len(name) > 255 or "/" in name or "\\" in name or "\x00" in name:
        return jsonify({"error": "Invalid name"}), 400

    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc or doc.get("deleted_at"):
            return jsonify({"error": "not found"}), 404
        cache_db.rename_document(conn, doc_id, name)
        sharing.update_shares_on_rename(doc_id, name)
        resync_svc.inject_metadata_from_doc_row(user_id, account_id, cache_db.get_document(conn, doc_id))
        return jsonify({"ok": True, "name": name})
    finally:
        conn.close()


@docs_bp.route("/docs/<doc_id>/delete", methods=["POST"])
@require_customer
def delete(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("docs.index"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc:
            return redirect(url_for("docs.index"))
        if doc.get("deleted_at"):
            cache_db.hard_delete_document(conn, doc_id)
            storage.delete_file(user_id, account_id, doc_id)
            sharing.revoke_shares_for_doc(doc_id)
        else:
            cache_db.soft_delete_document(conn, doc_id)
            sharing.revoke_shares_for_doc(doc_id)
            resync_svc.inject_metadata_from_doc_row(user_id, account_id, cache_db.get_document(conn, doc_id))
        return redirect(url_for("docs.index"))
    finally:
        conn.close()


@docs_bp.route("/docs/<doc_id>/restore", methods=["POST"])
@require_customer
def restore(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("docs.index"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc:
            return redirect(url_for("docs.index"))
        cache_db.restore_document(conn, doc_id)
        resync_svc.inject_metadata_from_doc_row(user_id, account_id, cache_db.get_document(conn, doc_id))
        return redirect(url_for("docs.index"))
    finally:
        conn.close()


@docs_bp.route("/docs/<doc_id>/download")
@require_customer
def download(doc_id):
    user_id = session.get("user_id")
    account_id = _resolve_account_id()
    if not account_id:
        return redirect(url_for("docs.index"))

    account = _get_account(account_id, user_id)

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc or doc.get("deleted_at"):
            return redirect(url_for("docs.index"))

        data = storage.read_file(user_id, account_id, doc_id)
        if data is None:
            return redirect(url_for("docs.index"))

        ext = doc.get("original_format") or doc["doc_type"]
        filename = f"{doc['name']}.{ext}"
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        return send_file(
            io.BytesIO(data),
            mimetype=mime,
            as_attachment=True,
            download_name=filename,
        )
    finally:
        conn.close()


def _is_ajax():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _target_doc_type(ext):
    return target_odf_type(ext) or "odt"


def _get_user_emails(user_id):
    accounts = CustomerAccount.query.filter_by(
        customer_id=user_id, is_active=True,
    ).all()
    return [a.email_address.lower() for a in accounts]


@docs_bp.route("/docs/upload", methods=["POST"])
@require_customer
def upload():
    ajax = _is_ajax()
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        if ajax:
            return jsonify({"error": "No active account"}), 400
        return redirect(url_for("docs.index"))

    account = _get_account(account_id, user_id)

    if "file" not in request.files:
        if ajax:
            return jsonify({"error": "No file provided"}), 400
        return redirect(url_for("docs.index"))

    f = request.files["file"]
    if not f.filename:
        if ajax:
            return jsonify({"error": "No filename provided"}), 400
        return redirect(url_for("docs.index"))

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALL_UPLOAD_EXTENSIONS:
        if ajax:
            return jsonify({"error": f"Unsupported file type .{ext}"}), 400
        return redirect(url_for("docs.index"))

    f.seek(0, 2)
    file_size = f.tell()
    f.seek(0)
    if file_size > MAX_UPLOAD_BYTES:
        if ajax:
            return jsonify({"error": "File exceeds 50 MB limit"}), 400
        return redirect(url_for("docs.index"))

    doc_id = uuid.uuid4().hex
    target_type = _target_doc_type(ext)
    original_format = ext if ext not in ("odt", "ods", "odp") else None
    name = f.filename.rsplit(".", 1)[0] if "." in f.filename else f.filename

    folder = (request.form.get("folder") or "").strip().strip("/")
    if folder:
        try:
            for seg in folder.split("/"):
                folders_svc.validate_folder_name(seg)
            folders_svc.assert_depth(folder)
        except folders_svc.FolderError:
            folder = ""

    conn = _open_cache_for_account(account)
    if not conn:
        if ajax:
            return jsonify({"error": "Could not open document store"}), 500
        return redirect(url_for("mail.login"))

    try:
        if folder:
            folders_svc.ensure_folder_path(conn, account_id, folder)
        if ext in ("odt", "ods", "odp"):
            file_data = f.read()
            cache_db.create_document(conn, doc_id, name, target_type, account_id, file_size=0, folder_path=folder)
            metadata = resync_svc.build_doc_metadata(doc_id, name, target_type, account_id, folder_path=folder)
            file_data = doc_meta.inject_metadata(file_data, metadata)
            storage.write_file(user_id, account_id, doc_id, file_data)
        elif ext in PANDOC_UPLOAD_EXTENSIONS:
            from app.shared.pandoc_formats import convert_to_odf as pandoc_convert, PANDOC_EXTENSIONS
            raw_data = f.read()
            pandoc_reader = PANDOC_EXTENSIONS.get(ext, {}).get("pandoc_reader", "plain")
            converted = pandoc_convert(raw_data, pandoc_reader, target_type)
            if not converted:
                raise collabora.ConversionError(f"Could not convert .{ext} file with pandoc")
            file_data = converted
            cache_db.create_document(conn, doc_id, name, target_type, account_id, file_size=0, folder_path=folder)
            metadata = resync_svc.build_doc_metadata(doc_id, name, target_type, account_id, folder_path=folder)
            file_data = doc_meta.inject_metadata(file_data, metadata)
            storage.write_file(user_id, account_id, doc_id, file_data)
        else:
            raw_data = f.read()
            cache_db.create_document(conn, doc_id, name, target_type, account_id, file_size=0, original_format=original_format, folder_path=folder)
            metadata = resync_svc.build_doc_metadata(doc_id, name, target_type, account_id, original_format=original_format, folder_path=folder)
            storage.write_file(user_id, account_id, doc_id, raw_data)
            storage.write_sidecar(user_id, account_id, doc_id, metadata)
            file_data = raw_data
        cache_db.update_file_size(conn, doc_id, len(file_data))
    except (collabora.ConversionError, Exception) as exc:
        if not isinstance(exc, collabora.ConversionError):
            logger.exception("Unexpected error during conversion of %s", f.filename)
        else:
            logger.error("Conversion failed for %s: %s", f.filename, exc)
        if ajax:
            return jsonify({"error": f"Could not convert {f.filename}. Please try uploading an .odt file or try again later."}), 500
        return redirect(url_for("docs.index"))
    finally:
        conn.close()

    if ajax:
        return jsonify({"doc_id": doc_id, "editor_url": url_for("docs.editor", doc_id=doc_id, _external=False)})
    return redirect(url_for("docs.editor", doc_id=doc_id))


@docs_bp.route("/docs/trash/empty", methods=["POST"])
@require_customer
def empty_trash():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("docs.index"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        trash = cache_db.list_trash(conn, account_id)
        for doc in trash:
            cache_db.hard_delete_document(conn, doc["id"])
            storage.delete_file(user_id, account_id, doc["id"])
            sharing.revoke_shares_for_doc(doc["id"])
        return redirect(url_for("docs.index"))
    finally:
        conn.close()


@docs_bp.route("/docs/<doc_id>/convert", methods=["POST"])
@require_customer
def convert(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"error": "no account"}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": "unauthorized"}), 401

    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc or doc.get("deleted_at"):
            return jsonify({"error": "not found"}), 404

        original_format = doc.get("original_format")
        if not original_format:
            return jsonify({"error": "document is already editable"}), 400

        raw_data = storage.read_file(user_id, account_id, doc_id)
        if raw_data is None:
            return jsonify({"error": "file not found"}), 404

        target_type = target_odf_type(original_format) or doc["doc_type"]

        if original_format in PANDOC_UPLOAD_EXTENSIONS:
            from app.shared.pandoc_formats import convert_to_odf as pandoc_convert, PANDOC_EXTENSIONS
            pandoc_reader = PANDOC_EXTENSIONS.get(original_format, {}).get("pandoc_reader", "plain")
            converted = pandoc_convert(raw_data, pandoc_reader, target_type)
            if not converted:
                return jsonify({"error": f"Could not convert .{original_format} file"}), 500
            file_data = converted
        else:
            converted = collabora.convert_upload(
                io.BytesIO(raw_data), f"{doc['name']}.{original_format}", target_type,
            )
            file_data = converted.read()

        new_doc_id = uuid.uuid4().hex
        cache_db.create_document(conn, new_doc_id, doc["name"], target_type, account_id, file_size=0)
        metadata = resync_svc.build_doc_metadata(new_doc_id, doc["name"], target_type, account_id)
        file_data = doc_meta.inject_metadata(file_data, metadata)
        storage.write_file(user_id, account_id, new_doc_id, file_data)
        cache_db.update_file_size(conn, new_doc_id, len(file_data))

        return jsonify({"doc_id": new_doc_id, "editor_url": url_for("docs.editor", doc_id=new_doc_id)})
    except collabora.ConversionError as exc:
        logger.error("Conversion failed for doc_id=%s: %s", doc_id, exc)
        return jsonify({"error": f"Conversion failed: {exc}"}), 500
    except Exception:
        logger.exception("Unexpected error converting doc_id=%s", doc_id)
        return jsonify({"error": "Unexpected error during conversion"}), 500
    finally:
        conn.close()


@docs_bp.route("/docs/sync", methods=["POST"])
@require_customer
def sync():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("docs.index"))

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        resync_svc.resync_docs(conn, user_id, account_id)
    finally:
        conn.close()

    return redirect(url_for("docs.index", resync=1))


# ---------------------------------------------------------------------------
# Folders & tags
# ---------------------------------------------------------------------------

def _form_or_json(*keys):
    """Read values from form data, falling back to a JSON body. Returns a dict."""
    out = {}
    for k in keys:
        if k in request.form:
            out[k] = request.form.get(k)
            continue
    if not out and request.is_json:
        data = request.get_json(silent=True) or {}
        for k in keys:
            if k in data:
                out[k] = data[k]
    return out


def _folder_action_required(account_id):
    if not account_id:
        return jsonify({"error": {"code": "NO_ACCOUNT", "message": "No account selected."}}), 400
    return None


@docs_bp.route("/docs/folders", methods=["GET"])
@require_customer
def list_folders():
    """Return the folder tree as JSON (for AJAX sidebar rendering)."""
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    try:
        tree = folders_svc.list_tree(conn, account_id)
    finally:
        conn.close()
    return jsonify({"folders": tree, "account_id": account_id})


@docs_bp.route("/docs/folders", methods=["POST"])
@require_customer
def create_folder():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    data = _form_or_json("name", "parent")
    name = (data.get("name") or "").strip()
    parent = (data.get("parent") or "").strip().strip("/")
    try:
        path = folders_svc.normalize_path(parent, name)
        folders_svc.assert_depth(path)
    except folders_svc.FolderError as exc:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    try:
        folders_svc.ensure_folder_path(conn, account_id, path)
        folder = cache_db.get_folder_by_path(conn, account_id, path)
    finally:
        conn.close()
    return jsonify({"ok": True, "path": path, "name": folders_svc.leaf_name(path), "id": (folder or {}).get("id")})


@docs_bp.route("/docs/folders/rename", methods=["POST"])
@require_customer
def rename_folder():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    data = _form_or_json("path", "name")
    path = (data.get("path") or "").strip().strip("/")
    new_name = (data.get("name") or "").strip()
    if not path:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "path is required"}}), 400
    try:
        new_name = folders_svc.validate_folder_name(new_name)
    except folders_svc.FolderError as exc:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}), 400
    new_path = folders_svc.normalize_path(folders_svc.parent_path(path), new_name)

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    try:
        cache_db.rename_folder_subtree(conn, account_id, path, new_path)
        # Re-inject embedded metadata so the move survives a resync.
        for doc in cache_db.subtree_documents(conn, account_id, new_path):
            if doc.get("deleted_at"):
                continue
            resync_svc.inject_metadata_from_doc_row(user_id, account_id, doc)
    finally:
        conn.close()
    return jsonify({"ok": True, "path": new_path, "name": new_name})


@docs_bp.route("/docs/folders/delete", methods=["POST"])
@require_customer
def delete_folder():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    data = _form_or_json("path")
    path = (data.get("path") or "").strip().strip("/")
    if not path:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "path is required"}}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    parent = folders_svc.parent_path(path)
    try:
        # Capture undo state before mutating.
        moved_docs = [
            {"id": d["id"], "old_path": d.get("folder_path", "")}
            for d in cache_db.subtree_documents(conn, account_id, path)
            if not d.get("deleted_at")
        ]
        deleted_rows = [
            {"path": r["path"], "name": r["name"]}
            for r in cache_db.list_folders(conn, account_id)
            if r["path"] == path or r["path"].startswith(path + "/")
        ]
        # Flatten documents to the parent folder, then drop folder rows.
        cache_db.move_subtree_docs_to_parent(conn, account_id, path, parent)
        cache_db.delete_folder_subtree_rows(conn, account_id, path)
        for d in moved_docs:
            doc = cache_db.get_document(conn, d["id"])
            if doc and not doc.get("deleted_at"):
                resync_svc.inject_metadata_from_doc_row(user_id, account_id, doc)
        session["docs_folder_undo"] = {
            "deleted_rows": deleted_rows,
            "moved_docs": moved_docs,
            "parent": parent,
        }
    finally:
        conn.close()
    return jsonify({"ok": True, "path": path, "moved_to": parent, "undo": True})


@docs_bp.route("/docs/folders/delete/undo", methods=["POST"])
@require_customer
def undo_delete_folder():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    undo = session.pop("docs_folder_undo", None)
    if not undo:
        return jsonify({"error": {"code": "NO_UNDO", "message": "Nothing to undo."}}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    try:
        # Restore folder rows.
        for r in undo.get("deleted_rows", []):
            try:
                folders_svc.ensure_folder_path(conn, account_id, r["path"])
            except folders_svc.FolderError:
                logger.warning("undo_delete_folder: skipping invalid path %r", r["path"])
        # Restore each document's original folder_path.
        for d in undo.get("moved_docs", []):
            doc = cache_db.get_document(conn, d["id"])
            if doc and not doc.get("deleted_at"):
                cache_db.set_document_folder(conn, d["id"], d["old_path"])
                resync_svc.inject_metadata_from_doc_row(user_id, account_id, cache_db.get_document(conn, d["id"]))
    finally:
        conn.close()
    return jsonify({"ok": True})


@docs_bp.route("/docs/<doc_id>/move", methods=["POST"])
@require_customer
def move_document(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    data = _form_or_json("folder")
    target = (data.get("folder") or "").strip().strip("/")
    if target:
        try:
            for seg in target.split("/"):
                folders_svc.validate_folder_name(seg)
            folders_svc.assert_depth(target)
        except folders_svc.FolderError as exc:
            return jsonify({"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}), 400

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc or doc.get("deleted_at"):
            return jsonify({"error": {"code": "NOT_FOUND", "message": "Document not found"}}), 404
        if target:
            folders_svc.ensure_folder_path(conn, account_id, target)
        cache_db.set_document_folder(conn, doc_id, target)
        resync_svc.inject_metadata_from_doc_row(user_id, account_id, cache_db.get_document(conn, doc_id))
    finally:
        conn.close()
    return jsonify({"ok": True, "folder_path": target})


@docs_bp.route("/docs/<doc_id>/tags", methods=["GET"])
@require_customer
def get_tags(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc:
            return jsonify({"error": {"code": "NOT_FOUND", "message": "Document not found"}}), 404
        tags = cache_db.get_document_tags(conn, doc_id)
    finally:
        conn.close()
    return jsonify({"tags": tags})


@docs_bp.route("/docs/<doc_id>/tags", methods=["POST"])
@require_customer
def update_tags(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    err = _folder_action_required(account_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    add_raw = data.get("add") or []
    remove_raw = data.get("remove") or []
    if data.get("set") is not None:
        desired = data.get("set") or []
        if not isinstance(desired, list):
            return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "set must be a list"}}), 400
        add_raw = desired
        remove_raw = None
    if not isinstance(add_raw, list) or (remove_raw is not None and not isinstance(remove_raw, list)):
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "add/remove must be lists"}}), 400

    add_tags, remove_tags = [], remove_raw or []
    for t in add_raw:
        t = str(t).strip()
        if t and len(t) <= 50 and t not in add_tags:
            add_tags.append(t)

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": {"code": "NO_CACHE", "message": "Unable to open document store."}}), 503
    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc or doc.get("deleted_at"):
            return jsonify({"error": {"code": "NOT_FOUND", "message": "Document not found"}}), 404
        if data.get("set") is not None:
            cache_db.set_document_tags(conn, doc_id, add_tags)
        else:
            cache_db.update_document_tags(conn, doc_id, add=add_tags, remove=remove_tags)
        resync_svc.inject_metadata_from_doc_row(user_id, account_id, cache_db.get_document(conn, doc_id))
        tags = cache_db.get_document_tags(conn, doc_id)
    finally:
        conn.close()
    return jsonify({"ok": True, "tags": tags})
