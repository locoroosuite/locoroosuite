import os
import time

from flask import request, jsonify, Response

from app.modules.docs.controllers.helpers import docs_bp, logger
from app.modules.docs.services import wopi_token, cache_db, storage, resync as resync_svc


def _extract_token():
    token = request.args.get("access_token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    token = request.form.get("access_token")
    if token:
        return token
    logger.debug(
        "WOPI no token found: args=%s headers=%s form=%s",
        list(request.args.keys()),
        list(request.headers.keys()),
        list(request.form.keys()),
    )
    return None


def _is_share_access(payload):
    return payload.get("share_access") is True


@docs_bp.route("/docs/wopi/files/<doc_id>", methods=["GET", "POST"])
def wopi_check_file_info(doc_id):
    token_str = _extract_token()
    if not token_str:
        return jsonify({"error": "missing token"}), 401

    payload = wopi_token.validate_token(token_str)
    if not payload:
        return jsonify({"error": "invalid or expired token"}), 401

    if payload["doc_id"] != doc_id:
        return jsonify({"error": "token scope mismatch"}), 403

    if _is_share_access(payload):
        return _wopi_check_file_info_share(doc_id, payload)

    user_id = payload["user_id"]
    account_id = payload["account_id"]
    key_hex = _get_key_for_user(user_id)
    if not key_hex:
        return jsonify({"error": "unauthorized"}), 401

    from app.shared.models.core import CustomerAccount
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=user_id, is_active=True
    ).first()
    if not account:
        return jsonify({"error": "account not found"}), 404

    conn = _open_cache(account, key_hex)
    if not conn:
        return jsonify({"error": "cache unavailable"}), 500

    try:
        doc = cache_db.get_document(conn, doc_id)
        if not doc:
            return jsonify({"error": "document not found"}), 404

        file_size = 0
        if storage.file_exists(user_id, account_id, doc_id):
            path = storage._storage_path(user_id, account_id, doc_id)
            file_size = os.path.getsize(path)

        owner_name = account.email_address.split("@")[0]

        original_format = doc.get("original_format")
        is_original = original_format is not None
        base_ext = original_format if is_original else doc["doc_type"]
        base_name = f"{doc['name']}.{base_ext}"

        writable = payload.get("writable", True) and not is_original

        info = {
            "BaseFileName": base_name,
            "Size": file_size,
            "OwnerId": str(user_id),
            "UserId": str(user_id),
            "UserFriendlyName": owner_name,
            "UserCanWrite": writable,
            "ReadOnly": not writable,
            "LastModifiedTime": doc.get("updated_at", ""),
            "SupportsUpdate": not is_original,
            "SupportsLocks": False,
            "SupportsRename": not is_original,
            "access_token_ttl": str((payload["exp"] - int(time.time())) * 1000),
        }
        return jsonify(info)
    finally:
        conn.close()


def _wopi_check_file_info_share(doc_id, payload):
    from app.shared.models.core import DocShare
    share_id = payload.get("share_id")
    share = DocShare.query.filter_by(
        id=share_id, doc_id=doc_id, revoked_at=None,
    ).first()
    if not share:
        return jsonify({"error": "share not found"}), 404

    owner_user_id = payload["owner_user_id"]
    owner_account_id = payload["owner_account_id"]

    file_size = share.doc_size or 0
    if storage.file_exists(owner_user_id, owner_account_id, doc_id):
        path = storage._storage_path(owner_user_id, owner_account_id, doc_id)
        file_size = os.path.getsize(path)

    writable = payload.get("writable", False)
    info = {
        "BaseFileName": f"{share.doc_name or 'Document'}.{share.doc_type or 'odt'}",
        "Size": file_size,
        "OwnerId": str(owner_user_id),
        "UserId": "share-" + str(share.id),
        "UserFriendlyName": share.recipient_email or "Guest",
        "UserCanWrite": writable,
        "ReadOnly": not writable,
        "LastModifiedTime": share.doc_updated_at or "",
        "SupportsUpdate": writable,
        "SupportsLocks": False,
        "SupportsRename": False,
        "access_token_ttl": str((payload["exp"] - int(time.time())) * 1000),
    }
    return jsonify(info)


@docs_bp.route("/docs/wopi/files/<doc_id>/contents", methods=["GET"])
def wopi_get_file(doc_id):
    token_str = _extract_token()
    if not token_str:
        return jsonify({"error": "missing token"}), 401

    payload = wopi_token.validate_token(token_str)
    if not payload:
        return jsonify({"error": "invalid or expired token"}), 401

    if payload["doc_id"] != doc_id:
        return jsonify({"error": "token scope mismatch"}), 403

    if _is_share_access(payload):
        return _wopi_get_file_share(doc_id, payload)

    user_id = payload["user_id"]
    account_id = payload["account_id"]

    data = storage.read_file(user_id, account_id, doc_id)
    if data is None:
        return jsonify({"error": "file not found"}), 404

    return Response(data, mimetype="application/octet-stream")


def _wopi_get_file_share(doc_id, payload):
    from app.shared.models.core import DocShare
    share_id = payload.get("share_id")
    share = DocShare.query.filter_by(
        id=share_id, doc_id=doc_id, revoked_at=None,
    ).first()
    if not share:
        return jsonify({"error": "share not found"}), 404

    from app.modules.docs.services.sharing import record_share_access
    record_share_access(share)

    owner_user_id = payload["owner_user_id"]
    owner_account_id = payload["owner_account_id"]
    data = storage.read_file(owner_user_id, owner_account_id, doc_id)
    if data is None:
        return jsonify({"error": "file not found"}), 404

    return Response(data, mimetype="application/octet-stream")


@docs_bp.route("/docs/wopi/files/<doc_id>/contents", methods=["POST"])
def wopi_put_file(doc_id):
    token_str = _extract_token()
    if not token_str:
        return jsonify({"error": "missing token"}), 401

    payload = wopi_token.validate_token(token_str)
    if not payload:
        return jsonify({"error": "invalid or expired token"}), 401

    if payload["doc_id"] != doc_id:
        return jsonify({"error": "token scope mismatch"}), 403

    if not payload.get("writable", True):
        return jsonify({"error": "read-only"}), 403

    if _is_share_access(payload):
        return _wopi_put_file_share(doc_id, payload)

    user_id = payload["user_id"]
    account_id = payload["account_id"]
    key_hex = _get_key_for_user(user_id)
    if not key_hex:
        return jsonify({"error": "unauthorized"}), 401

    file_data = request.get_data()

    from app.shared.models.core import CustomerAccount
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=user_id, is_active=True
    ).first()

    if account:
        conn = _open_cache(account, key_hex)
        if conn:
            try:
                doc = cache_db.get_document(conn, doc_id)
                if doc:
                    from app.modules.docs.services import doc_meta
                    metadata = resync_svc.build_doc_metadata(
                        doc_id=doc["id"], name=doc["name"],
                        doc_type=doc["doc_type"], account_id=doc["account_id"],
                        deleted_at=doc.get("deleted_at"),
                        created_at=doc.get("created_at"),
                        updated_at=doc.get("updated_at"),
                    )
                    try:
                        file_data = doc_meta.inject_metadata(file_data, metadata)
                    except Exception:
                        logger.warning("Failed to re-inject metadata on PutFile for doc_id=%s", doc_id)

                written_size = storage.write_file(user_id, account_id, doc_id, file_data)
                cache_db.update_file_size(conn, doc_id, written_size)
            finally:
                conn.close()
    else:
        written_size = storage.write_file(user_id, account_id, doc_id, file_data)

    return jsonify({"status": "ok"})


def _wopi_put_file_share(doc_id, payload):
    from app.shared.models.core import DocShare
    share_id = payload.get("share_id")
    share = DocShare.query.filter_by(
        id=share_id, doc_id=doc_id, revoked_at=None,
    ).first()
    if not share:
        return jsonify({"error": "share not found"}), 404

    if share.permission != "write":
        return jsonify({"error": "read-only share"}), 403

    owner_user_id = payload["owner_user_id"]
    owner_account_id = payload["owner_account_id"]

    file_data = request.get_data()
    written_size = storage.write_file(owner_user_id, owner_account_id, doc_id, file_data)

    from app.modules.docs.services.sharing import update_shares_on_save
    update_shares_on_save(doc_id, written_size)

    return jsonify({"status": "ok"})


def _get_key_for_user(user_id):
    from app.shared.keys import get_user_key
    return get_user_key(user_id)


def _open_cache(account, key_hex):
    from app.modules.docs.services.cache import get_cache_path
    from app.modules.docs.services.cache_db import open_cache
    path = get_cache_path(account)
    return open_cache(path, key_hex)
