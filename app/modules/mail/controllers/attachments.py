import logging
import re
import threading
import time
import uuid

from flask import current_app, jsonify, request, session

from app.modules.mail.controllers.helpers import mail_bp
from app.modules.mail.services import attachments as staging
from app.shared.auth import require_customer


logger = logging.getLogger(__name__)

_DEFAULT_MAX_FILE = 25 * 1024 * 1024
_DEFAULT_MAX_TOTAL = 50 * 1024 * 1024
_GC_INTERVAL_SECONDS = 600

_gc_lock = threading.Lock()
_last_gc = 0.0

_FILENAME_BAD = re.compile(r"[\x00-\x1f]")


def _max_file_bytes():
    try:
        return int(current_app.config.get("MAIL_ATTACHMENT_MAX_FILE_BYTES", _DEFAULT_MAX_FILE))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_FILE


def _max_total_bytes():
    try:
        return int(current_app.config.get("MAIL_ATTACHMENT_MAX_TOTAL_BYTES", _DEFAULT_MAX_TOTAL))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_TOTAL


def _ttl_hours():
    try:
        return int(current_app.config.get("MAIL_ATTACHMENT_STAGING_TTL_HOURS", 24))
    except (TypeError, ValueError):
        return 24


def _get_session_id():
    sid = (request.form.get("compose_session_id") or request.args.get("compose_session_id") or "").strip()
    if not staging.is_valid_id(sid):
        return None
    return sid


def _sanitize_filename(name):
    if not name:
        return "attachment"
    cleaned = name.replace("\\", "/").split("/")[-1].strip()
    cleaned = _FILENAME_BAD.sub("", cleaned)
    if not cleaned:
        cleaned = "attachment"
    return cleaned[:255]


def _maybe_gc():
    global _last_gc
    now = time.time()
    if now - _last_gc < _GC_INTERVAL_SECONDS:
        return
    acquired = _gc_lock.acquire(blocking=False)
    if not acquired:
        return
    try:
        if now - _last_gc < _GC_INTERVAL_SECONDS:
            return
        _last_gc = now
        removed = staging.cleanup_stale(_ttl_hours())
        if removed:
            logger.info("cleaned up %d stale attachment staging sessions", removed)
    finally:
        _gc_lock.release()


@mail_bp.route("/mail/attachments/stage", methods=["POST"])
@require_customer
def stage_attachment():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": {"code": "unauthorized", "message": "Session expired."}}), 401

    compose_session_id = _get_session_id()
    if not compose_session_id:
        return jsonify({
            "error": {"code": "invalid_session", "message": "Invalid or missing compose session."}
        }), 400

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({
            "error": {"code": "no_file", "message": "No file was provided."}
        }), 400

    raw = uploaded.read()
    size = len(raw)
    max_file = _max_file_bytes()
    if size <= 0:
        return jsonify({
            "error": {"code": "empty_file", "message": "The selected file is empty."}
        }), 400
    if size > max_file:
        return jsonify({
            "error": {
                "code": "file_too_large",
                "message": "This file exceeds the per-file size limit.",
                "limit": max_file,
                "size": size,
            }
        }), 413

    current_total = staging.session_size(user_id, compose_session_id)
    max_total = _max_total_bytes()
    if current_total + size > max_total:
        return jsonify({
            "error": {
                "code": "total_too_large",
                "message": "Adding this file exceeds the total attachment size limit.",
                "limit": max_total,
                "used": current_total,
            }
        }), 413

    file_id = uuid.uuid4().hex
    name = _sanitize_filename(uploaded.filename)
    mime = uploaded.mimetype or "application/octet-stream"
    try:
        staging.stage_file(user_id, compose_session_id, file_id, raw, name, mime)
    except Exception:
        logger.exception("failed to stage attachment user_id=%s", user_id)
        return jsonify({
            "error": {"code": "stage_failed", "message": "Unable to store the file. Please retry."}
        }), 500

    _maybe_gc()
    return jsonify({
        "id": file_id,
        "name": name,
        "size": size,
        "mime": mime,
        "compose_session_id": compose_session_id,
        "used": current_total + size,
        "limit": max_total,
    })


@mail_bp.route("/mail/attachments/<file_id>", methods=["DELETE"])
@require_customer
def delete_attachment(file_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": {"code": "unauthorized", "message": "Session expired."}}), 401

    compose_session_id = _get_session_id()
    if not compose_session_id or not staging.is_valid_id(file_id):
        return jsonify({
            "error": {"code": "invalid_request", "message": "Invalid attachment or session."}
        }), 400

    staging.delete_staged(user_id, compose_session_id, file_id)
    return jsonify({"ok": True})


@mail_bp.route("/mail/attachments", methods=["GET"])
@require_customer
def list_attachments():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": {"code": "unauthorized", "message": "Session expired."}}), 401

    compose_session_id = _get_session_id()
    if not compose_session_id:
        return jsonify({
            "error": {"code": "invalid_session", "message": "Invalid or missing compose session."}
        }), 400

    items = staging.list_staged(user_id, compose_session_id)
    used = sum(item["size"] for item in items)
    return jsonify({
        "attachments": items,
        "used": used,
        "limit": _max_total_bytes(),
    })
