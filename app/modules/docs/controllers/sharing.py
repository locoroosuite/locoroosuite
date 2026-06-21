import logging

from flask import request, jsonify, session, redirect, url_for, render_template, current_app, make_response
from urllib.parse import quote

from app.shared.auth import require_customer
from app.shared.models.core import CustomerAccount, DocShare
from app.modules.docs.controllers.helpers import docs_bp
from app.modules.docs.services import wopi_token, sharing, collabora

logger = logging.getLogger(__name__)


@docs_bp.route("/docs/<doc_id>/shares", methods=["GET"])
@require_customer
def list_shares(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"error": "no account"}), 400

    CustomerAccount.query.filter_by(
        id=account_id, customer_id=user_id, is_active=True,
    ).first_or_404()

    shares = sharing.get_active_shares_for_doc(doc_id)
    shares_owned = [s for s in shares if s.owner_user_id == user_id]
    result = []
    for s in shares_owned:
        result.append({
            "id": s.id,
            "recipient_email": s.recipient_email,
            "permission": s.permission,
            "share_type": s.share_type,
            "share_token": s.share_token,
            "view_count": s.view_count,
            "last_accessed_at": s.last_accessed_at.isoformat() if s.last_accessed_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return jsonify({"shares": result})


@docs_bp.route("/docs/<doc_id>/shares", methods=["POST"])
@require_customer
def add_shares(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify({"error": "no account"}), 400

    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=user_id, is_active=True,
    ).first_or_404()

    from app.modules.docs.controllers.helpers import _open_cache_for_account
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"error": "unauthorized"}), 401

    try:
        from app.modules.docs.services import cache_db
        doc = cache_db.get_document(conn, doc_id)
        if not doc or doc.get("deleted_at"):
            return jsonify({"error": "document not found"}), 404
    finally:
        conn.close()

    data = request.get_json(silent=True) or {}
    emails_str = data.get("emails", "")
    permission = data.get("permission", "view")
    send_invite = data.get("send_invite", True)

    if permission not in ("view", "write"):
        return jsonify({"error": "Invalid permission"}), 400

    emails = [e.strip() for e in emails_str.split(",") if e.strip()]
    if not emails:
        return jsonify({"error": "No email addresses provided"}), 400

    created = sharing.create_shares_batch(
        doc_id=doc_id,
        owner_user_id=user_id,
        owner_account_id=account_id,
        recipients=emails,
        permission=permission,
        doc_name=doc["name"],
        doc_type=doc["doc_type"],
        doc_size=doc.get("file_size", 0),
        doc_updated_at=doc.get("updated_at"),
    )

    if send_invite:
        for share in created:
            sharing.send_share_invite(share, account.email_address)

    result = []
    for s in created:
        result.append({
            "id": s.id,
            "recipient_email": s.recipient_email,
            "permission": s.permission,
            "share_type": s.share_type,
        })

    return jsonify({"shares": result}), 201


@docs_bp.route("/docs/<doc_id>/shares/<int:share_id>", methods=["DELETE"])
@require_customer
def revoke_share(doc_id, share_id):
    user_id = session.get("user_id")
    ok = sharing.revoke_share(share_id, user_id)
    if not ok:
        return jsonify({"error": "share not found or not owned"}), 404
    return jsonify({"ok": True})


@docs_bp.route("/docs/s/<share_token>")
def public_share_view(share_token):
    share = sharing.get_share_by_token(share_token)
    if not share:
        return render_template("docs_share_error.html", message="This link has been revoked or does not exist."), 404

    sharing.record_share_access(share)

    writable = share.permission == "write"

    token = wopi_token.generate_share_token(
        doc_id=share.doc_id,
        owner_user_id=share.owner_user_id,
        owner_account_id=share.owner_account_id,
        share_id=share.id,
        writable=writable,
    )

    collabora_internal = (
        current_app.config.get("COLLABORA_INTERNAL_URL")
        or current_app.config.get("COLLABORA_URL", "http://localhost:9980")
    )
    collabora_public = (
        current_app.config.get("COLLABORA_PUBLIC_URL")
        or collabora_internal
    )

    wopi_host_url = current_app.config.get("WOPI_HOST_URL", "")
    if not wopi_host_url:
        wopi_host_url = request.host_url.rstrip("/")
    wopi_src = wopi_host_url + url_for("docs.wopi_check_file_info", doc_id=share.doc_id)
    edit_base = collabora.get_edit_url(share.doc_type or "odt", collabora_internal) or f"{collabora_public}/browser/dist/cool.html?"

    edit_base_http = edit_base.replace("https://", "http://", 1)
    if edit_base_http.startswith(collabora_internal):
        edit_base = collabora_public + edit_base_http[len(collabora_internal):]

    collabora_src = (
        f"{edit_base}"
        f"WOPISrc={quote(wopi_src, safe='')}"
        f"&access_token={quote(token, safe='')}"
    )

    response = make_response(render_template(
        "docs_share_view.html",
        share=share,
        collabora_src=collabora_src,
        token=token,
    ))
    response.set_cookie(
        "share_access",
        share_token,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=28800,
    )
    return response


@docs_bp.route("/docs/<doc_id>/open-shared")
@require_customer
def open_shared_doc(doc_id):
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("docs.index"))

    user_emails = _get_user_emails(user_id)
    share = DocShare.query.filter_by(
        doc_id=doc_id, revoked_at=None,
    ).filter(DocShare.recipient_email.in_(user_emails)).first()

    if not share:
        return redirect(url_for("docs.index"))

    writable = share.permission == "write"

    token = wopi_token.generate_share_token(
        doc_id=share.doc_id,
        owner_user_id=share.owner_user_id,
        owner_account_id=share.owner_account_id,
        share_id=share.id,
        writable=writable,
    )

    collabora_internal = (
        current_app.config.get("COLLABORA_INTERNAL_URL")
        or current_app.config.get("COLLABORA_URL", "http://localhost:9980")
    )
    collabora_public = (
        current_app.config.get("COLLABORA_PUBLIC_URL")
        or collabora_internal
    )

    wopi_host_url = current_app.config.get("WOPI_HOST_URL", "")
    if not wopi_host_url:
        wopi_host_url = request.host_url.rstrip("/")
    wopi_src = wopi_host_url + url_for("docs.wopi_check_file_info", doc_id=share.doc_id)
    edit_base = collabora.get_edit_url(share.doc_type or "odt", collabora_internal) or f"{collabora_public}/browser/dist/cool.html?"

    edit_base_http = edit_base.replace("https://", "http://", 1)
    if edit_base_http.startswith(collabora_internal):
        edit_base = collabora_public + edit_base_http[len(collabora_internal):]

    collabora_src = (
        f"{edit_base}"
        f"WOPISrc={quote(wopi_src, safe='')}"
        f"&access_token={quote(token, safe='')}"
    )

    from app.shared.models.core import CustomerAccount
    owner_account = CustomerAccount.query.get(share.owner_account_id)

    return render_template(
        "docs_share_view.html",
        share=share,
        collabora_src=collabora_src,
        token=token,
        owner_email=owner_account.email_address if owner_account else "",
    )


def _get_user_emails(user_id):
    accounts = CustomerAccount.query.filter_by(
        customer_id=user_id, is_active=True,
    ).all()
    return [a.email_address.lower() for a in accounts]
