import json
import uuid

from flask import current_app, jsonify, redirect, render_template, request, session, url_for

from app.shared.db import db
from app.shared.models.imports import ImportRequest, ImportRun
from app.admin.services.google_import import build_google_auth_url, exchange_google_code, gmail_profile
from app.admin.services.import_security import (
    build_import_token,
    encrypt_import_secret,
    is_request_expired,
    parse_import_token,
)
from app.admin.services.takeout_uploads import append_upload_chunk, ensure_upload_metadata, finalize_upload
from app.shared.audit import log_audit
from app.admin import imports_bp


def _google_import_ready():
    return bool(
        current_app.config.get("GOOGLE_IMPORT_CLIENT_ID")
        and current_app.config.get("GOOGLE_IMPORT_CLIENT_SECRET")
    )


def _sanitize_error(message):
    text = (message or "").strip()
    return text[:240] if text else None


def _token_for_request(import_request):
    return build_import_token(import_request)


def _request_from_token(token):
    parsed = parse_import_token(token)
    if not parsed:
        return None, "Import link is invalid."
    import_request = db.session.get(ImportRequest, parsed.get("id"))
    if not import_request or import_request.link_key != parsed.get("key"):
        return None, "Import link is invalid."
    if not import_request.is_enabled:
        return None, "Import link has been disabled."
    if is_request_expired(import_request):
        import_request.status = "expired"
        db.session.commit()
        return None, "Import link has expired."
    return import_request, None


def _latest_run(import_request_id):
    return (
        ImportRun.query.filter_by(import_request_id=import_request_id)
        .order_by(ImportRun.started_at.desc(), ImportRun.id.desc())
        .first()
    )


def _serialize_upload(import_request):
    return {
        "filename": import_request.upload_filename,
        "uploaded_bytes": int(import_request.uploaded_bytes or 0),
        "total_bytes": int(import_request.upload_size_bytes or 0),
        "upload_status": import_request.upload_status or "none",
    }


@imports_bp.route("/r/<token>")
def view_request(token):
    import_request, error = _request_from_token(token)
    latest_run = _latest_run(import_request.id) if import_request else None
    folder_counts = {}
    if latest_run and latest_run.folder_counts_json:
        try:
            folder_counts = json.loads(latest_run.folder_counts_json)
        except ValueError:
            folder_counts = {}
    return render_template(
        "imports/request.html",
        title="Mailbox Import",
        import_request=import_request,
        token=token,
        latest_run=latest_run,
        folder_counts=folder_counts,
        google_ready=_google_import_ready(),
        upload_state=_serialize_upload(import_request) if import_request else {},
        page_error=error or request.args.get("error"),
        page_notice=request.args.get("notice"),
    )


@imports_bp.route("/r/<token>/google/start", methods=["POST"])
def start_google_oauth(token):
    import_request, error = _request_from_token(token)
    if error:
        return redirect(url_for("imports.view_request", token=token, error=error))
    if import_request.source_type != "google":
        return redirect(url_for("imports.view_request", token=token, error="This import link expects a Google Takeout upload, not Google OAuth."))
    if not _google_import_ready():
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                error="Google import is not configured on this server yet.",
            )
        )
    state = uuid.uuid4().hex
    session["import_oauth_state"] = state
    session["import_request_id"] = import_request.id
    auth_url = build_google_auth_url(
        current_app.config["GOOGLE_IMPORT_CLIENT_ID"],
        url_for("imports.google_callback", _external=True),
        state,
        current_app.config["GOOGLE_IMPORT_SCOPES"],
    )
    log_audit(
        None,
        "import_link",
        "import_oauth_start",
        f"import_request={import_request.id}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    return redirect(auth_url)


@imports_bp.route("/google/callback")
def google_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or state != session.get("import_oauth_state"):
        return render_template(
            "imports/request.html",
            title="Mailbox Import",
            import_request=None,
            token=None,
            latest_run=None,
            folder_counts={},
            google_ready=_google_import_ready(),
            page_error="Google authorization could not be verified.",
            page_notice=None,
        )

    import_request = db.session.get(ImportRequest, session.get("import_request_id"))
    if not import_request:
        return render_template(
            "imports/request.html",
            title="Mailbox Import",
            import_request=None,
            token=None,
            latest_run=None,
            folder_counts={},
            google_ready=_google_import_ready(),
            page_error="Import request no longer exists.",
            page_notice=None,
        )

    token = _token_for_request(import_request)
    if not import_request.is_enabled or is_request_expired(import_request):
        import_request.status = "disabled" if not import_request.is_enabled else "expired"
        db.session.commit()
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                error="This import link is no longer active.",
            )
        )
    if import_request.source_type != "google":
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                error="This import link expects a Google Takeout upload, not Google OAuth.",
            )
        )
    try:
        token_data = exchange_google_code(
            current_app.config["GOOGLE_IMPORT_CLIENT_ID"],
            current_app.config["GOOGLE_IMPORT_CLIENT_SECRET"],
            code,
            url_for("imports.google_callback", _external=True),
        )
        refresh_token = token_data.get("refresh_token")
        if not refresh_token and not import_request.encrypted_source_refresh_token:
            raise RuntimeError("Google did not return a reusable refresh token.")
        if refresh_token:
            import_request.encrypted_source_refresh_token = encrypt_import_secret(
                import_request.id,
                "source_refresh",
                refresh_token,
            )
        profile = gmail_profile(token_data["access_token"])
        import_request.source_account_email = profile.get("emailAddress")
        import_request.status = "ready"
        import_request.last_error = None
        db.session.commit()
        log_audit(
            None,
            "import_link",
            "import_oauth_success",
            f"import_request={import_request.id}",
            request.remote_addr,
            request.headers.get("User-Agent"),
        )
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                notice="Google access connected. You can start the import now.",
            )
        )
    except Exception as exc:
        import_request.status = "failed"
        import_request.last_error = _sanitize_error(str(exc)) or "Google authorization failed."
        db.session.commit()
        log_audit(
            None,
            "import_link",
            "import_oauth_failure",
            f"import_request={import_request.id}",
            request.remote_addr,
            request.headers.get("User-Agent"),
        )
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                error="Google authorization failed. Please retry.",
            )
        )
    finally:
        session.pop("import_oauth_state", None)
        session.pop("import_request_id", None)


@imports_bp.route("/r/<token>/run", methods=["POST"])
def run_request(token):
    import_request, error = _request_from_token(token)
    if error:
        return redirect(url_for("imports.view_request", token=token, error=error))
    if import_request.source_type == "google" and not _google_import_ready():
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                error="Google import is not configured on this server yet.",
            )
        )
    if import_request.status in ("queued", "running"):
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                notice="An import is already in progress for this link.",
            )
        )
    if import_request.source_type == "google" and not import_request.encrypted_source_refresh_token:
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                error="Connect Google before starting the import.",
            )
        )
    if import_request.source_type == "google_takeout" and (
        not import_request.staged_upload_path or import_request.upload_status != "uploaded"
    ):
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                error="Upload a Google Takeout MBOX file before starting the import.",
            )
        )

    run = ImportRun(import_request_id=import_request.id, status="queued", current_phase="queued")
    db.session.add(run)
    import_request.status = "queued"
    import_request.last_error = None
    db.session.commit()
    if not current_app.sync_manager.enqueue_import(import_request.id, run.id):
        db.session.delete(run)
        db.session.commit()
        return redirect(
            url_for(
                "imports.view_request",
                token=token,
                notice="An import is already queued for this link.",
            )
        )
    log_audit(
        None,
        "import_link",
        "import_run_start",
        f"import_request={import_request.id},run={run.id}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    return redirect(
        url_for(
            "imports.view_request",
            token=token,
            notice="Import queued. This page will refresh while it runs.",
        )
    )


@imports_bp.route("/r/<token>/takeout/upload/init", methods=["POST"])
def init_takeout_upload(token):
    import_request, error = _request_from_token(token)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    if import_request.source_type != "google_takeout":
        return jsonify({"ok": False, "error": "This link does not accept Takeout uploads."}), 400

    filename = (request.form.get("filename") or "").strip()
    try:
        total_size = int(request.form.get("total_size") or "0")
    except ValueError:
        total_size = 0
    if not filename or total_size <= 0:
        return jsonify({"ok": False, "error": "Select a valid MBOX file before uploading."}), 400
    if not filename.lower().endswith(".mbox"):
        return jsonify({"ok": False, "error": "Only MBOX files are supported for Google Takeout imports."}), 400

    ensure_upload_metadata(import_request, filename, total_size)
    import_request.status = "pending_upload" if import_request.uploaded_bytes == 0 else "uploading"
    import_request.last_error = None
    db.session.commit()
    log_audit(
        None,
        "import_link",
        "takeout_upload_start",
        f"import_request={import_request.id},bytes={import_request.upload_size_bytes}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    return jsonify({"ok": True, **_serialize_upload(import_request)})


@imports_bp.route("/r/<token>/takeout/upload/chunk", methods=["POST"])
def upload_takeout_chunk(token):
    import_request, error = _request_from_token(token)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    if import_request.source_type != "google_takeout":
        return jsonify({"ok": False, "error": "This link does not accept Takeout uploads."}), 400

    filename = (request.form.get("filename") or "").strip()
    upload_file = request.files.get("chunk")
    if not upload_file or not filename:
        return jsonify({"ok": False, "error": "Upload chunk missing."}), 400
    try:
        offset = int(request.form.get("offset") or "0")
        total_size = int(request.form.get("total_size") or "0")
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid upload metadata."}), 400
    is_last = request.form.get("is_last") == "1"

    try:
        ensure_upload_metadata(import_request, filename, total_size)
        _path, uploaded_bytes = append_upload_chunk(import_request, offset, upload_file.read())
        if is_last:
            finalize_upload(import_request)
            import_request.status = "ready"
            log_audit(
                None,
                "import_link",
                "takeout_upload_complete",
                f"import_request={import_request.id},bytes={import_request.upload_size_bytes}",
                request.remote_addr,
                request.headers.get("User-Agent"),
            )
        else:
            import_request.status = "uploading"
        import_request.last_error = None
        db.session.commit()
        return jsonify(
            {
                "ok": True,
                "complete": bool(is_last),
                **_serialize_upload(import_request),
            }
        )
    except Exception as exc:
        import_request.status = "pending_upload"
        import_request.last_error = _sanitize_error(str(exc)) or "Upload failed."
        db.session.commit()
        return jsonify(
            {
                "ok": False,
                "error": import_request.last_error,
                "expected_offset": int(import_request.uploaded_bytes or 0),
                **_serialize_upload(import_request),
            }
        ), 400
