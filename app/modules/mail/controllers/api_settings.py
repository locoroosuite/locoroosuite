from flask import session, request, redirect, url_for, render_template, flash

from app.shared.db import db
from app.shared.models.core import CustomerAccount, ApiToken
from app.shared.keys import get_user_key
from app.shared.auth import require_customer
from app.shared.audit import log_audit
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.controllers.helpers import mail_bp

from app.api.token_service import (
    create_api_token, revoke_api_token, ensure_api_enabled,
)


@mail_bp.route("/mail/settings/api", methods=["GET"])
@require_customer
def api_settings():
    user_id = session.get("user_id")
    accounts = CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).all()
    api_enabled = any(a.api_enabled for a in accounts)
    tokens = []
    if api_enabled:
        tokens = ApiToken.query.filter_by(customer_id=user_id).all()
    return render_template("api_settings.html", api_enabled=api_enabled, tokens=tokens, accounts=accounts)


@mail_bp.route("/mail/settings/api/enable", methods=["POST"])
@require_customer
def api_settings_enable():
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).first()
    if not account:
        flash("No active account found.")
        return redirect(url_for("mail.api_settings"))

    credential_key = get_user_key(user_id)
    if not credential_key:
        flash("Session key not found. Please log in again.")
        return redirect(url_for("mail.api_settings"))

    try:
        decrypt_with_key(account.encrypted_secret, credential_key)
    except Exception:
        flash("Incorrect password. Please try again.")
        return redirect(url_for("mail.api_settings"))

    ensure_api_enabled(user_id, credential_key)

    log_audit(user_id, "customer", "api_access_enable", "", request.remote_addr,
              request.headers.get("User-Agent", ""))
    flash("API access enabled. Create a token to get started.")
    return redirect(url_for("mail.api_settings"))


@mail_bp.route("/mail/settings/api/disable", methods=["POST"])
@require_customer
def api_settings_disable():
    user_id = session.get("user_id")

    credential_key = get_user_key(user_id)
    if not credential_key:
        flash("Session key not found. Please log in again.")
        return redirect(url_for("mail.api_settings"))

    for token in ApiToken.query.filter_by(customer_id=user_id).all():
        db.session.delete(token)

    for acc in CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).all():
        if acc.cache_db_path:
            from app.modules.mail.services.cache import purge_cache
            purge_cache(acc.cache_db_path)
            acc.cache_db_path = None
        acc.api_enabled = False
        acc.dek_wrapped_cred = None

    db.session.commit()
    session.clear()

    log_audit(user_id, "customer", "api_access_disable", "", request.remote_addr,
              request.headers.get("User-Agent", ""))
    return redirect(url_for("mail.login"))


@mail_bp.route("/mail/settings/api/tokens/create", methods=["POST"])
@require_customer
def api_settings_create_token():
    user_id = session.get("user_id")
    name = request.form.get("token_name", "").strip()
    if not name:
        flash("Token name is required.")
        return redirect(url_for("mail.api_settings"))

    scopes = []
    for module in ("mail", "contacts", "calendar", "docs"):
        read_on = request.form.get(f"scope_{module}_read") == "on"
        write_on = request.form.get(f"scope_{module}_write") == "on"
        if write_on:
            scopes.append(f"{module}:write")
            scopes.append(f"{module}:read")
        elif read_on:
            scopes.append(f"{module}:read")

    if not scopes:
        flash("At least one scope must be selected.")
        return redirect(url_for("mail.api_settings"))

    credential_key = get_user_key(user_id)
    if not credential_key:
        flash("Session key not found. Please log in again.")
        return redirect(url_for("mail.api_settings"))

    account = CustomerAccount.query.filter_by(customer_id=user_id, api_enabled=True, is_active=True).first()
    if not account:
        flash("API access is not enabled.")
        return redirect(url_for("mail.api_settings"))

    token_value, token_obj = create_api_token(user_id, credential_key, name, scopes)
    log_audit(user_id, "customer", "api_token_create", f"name={name}", request.remote_addr,
              request.headers.get("User-Agent", ""))
    return render_template("api_settings.html", api_enabled=True,
                           tokens=ApiToken.query.filter_by(customer_id=user_id).all(),
                           accounts=CustomerAccount.query.filter_by(customer_id=user_id, is_active=True).all(),
                           new_token=token_value, new_token_name=name)


@mail_bp.route("/mail/settings/api/tokens/<int:token_id>/revoke", methods=["POST"])
@require_customer
def api_settings_revoke_token(token_id):
    user_id = session.get("user_id")
    ok = revoke_api_token(token_id, user_id)
    if ok:
        log_audit(user_id, "customer", "api_token_revoke", f"token_id={token_id}",
                  request.remote_addr, request.headers.get("User-Agent", ""))
    return redirect(url_for("mail.api_settings"))
