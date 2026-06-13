from __future__ import annotations

import logging
import uuid
import time
from urllib.parse import urlparse

from flask import session, request, redirect, url_for, render_template, current_app

from app.shared.db import db
from app.shared.models.core import User, Domain, CustomerAccount
from app.modules.mail.services.crypto import derive_key
from app.modules.mail.services.secrets import encrypt_with_key
from app.modules.mail.services.imap_client import connect_imap, login_imap, safe_logout
from app.modules.mail.services.cache import build_cache_path
from app.shared.auth import require_customer
from app.shared.keys import set_user_key, clear_user_key

from app.modules.mail.controllers.helpers import mail_bp


logger = logging.getLogger(__name__)


def _is_safe_redirect_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc:
        server_name = current_app.config.get("SERVER_NAME", "")
        if server_name:
            return parsed.netloc == server_name or parsed.netloc.endswith("." + server_name)
        if current_app.config.get("APP_ENV") == "development":
            if parsed.netloc.endswith(".ngrok-free.dev") or parsed.netloc == request.host:
                return True
        return False
    return url.startswith("/") and not url.startswith("//")


def _resolve_login_key(account, credential_key, secret):
    if account.api_enabled and account.dek_wrapped_cred:
        from app.api.token_service import unwrap_dek_from_credential
        try:
            dek_hex = unwrap_dek_from_credential(account.dek_wrapped_cred, credential_key)
            return dek_hex
        except Exception:
            logger.warning("failed to unwrap DEK for account_id=%s, falling back to credential key", account.id)
    return credential_key


@mail_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if not Domain.query.first():
            return redirect(url_for("auth.setup"))
        if "user_id" in session and session.get("role") == "customer":
            uid = session["user_id"]
            acct_id = session.get("active_account_id")
            if acct_id:
                return redirect(url_for("mail.folder_view", account_id=acct_id, folder="INBOX"))
            acct = CustomerAccount.query.filter_by(customer_id=uid).first()
            if acct:
                session["active_account_id"] = acct.id
                return redirect(url_for("mail.folder_view", account_id=acct.id, folder="INBOX"))
        next_url = request.args.get("next", "")
        return render_template("login.html", next=next_url)

    request_id = uuid.uuid4().hex[:8]
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    next_url = request.form.get("next", "")
    redacted_email = email.split("@")[0][:2] + "***@" + email.split("@")[-1] if "@" in email else "***"
    logger.info("login start request_id=%s email=%s", request_id, redacted_email)

    domain_name = email.split("@")[-1]
    domain = Domain.query.filter_by(name=domain_name, is_active=True).first()
    if not domain:
        logger.info("login domain disabled request_id=%s domain=%s", request_id, domain_name)
        return render_template("login.html", error="Domain not enabled.", next=next_url)

    try:
        t0 = time.monotonic()
        client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
        logger.info(
            "login imap connected request_id=%s host=%s port=%s tls=%s elapsed_ms=%s",
            request_id,
            domain.imap_host,
            domain.imap_port,
            domain.imap_tls,
            int((time.monotonic() - t0) * 1000),
        )
        t1 = time.monotonic()
        login_imap(client, email, password=password)
        logger.info(
            "login imap authenticated request_id=%s elapsed_ms=%s",
            request_id,
            int((time.monotonic() - t1) * 1000),
        )
        t2 = time.monotonic()
        safe_logout(client)
        logger.info(
            "login imap logout request_id=%s elapsed_ms=%s",
            request_id,
            int((time.monotonic() - t2) * 1000),
        )
    except Exception:
        logger.exception("login imap failed request_id=%s", request_id)
        return render_template("login.html", error="IMAP authentication failed.", next=next_url)

    customer = User.query.filter_by(email=email).first()
    if customer and not customer.is_active:
        logger.info("login account deactivated request_id=%s user_id=%s", request_id, customer.id)
        return render_template("login.html", error="Account deactivated.", next=next_url)
    if not customer:
        customer = User(role="customer", email=email)
        db.session.add(customer)
        db.session.commit()
        logger.info("login customer created request_id=%s user_id=%s", request_id, customer.id)

    account = CustomerAccount.query.filter_by(customer_id=customer.id, email_address=email).first()
    if not account:
        account = CustomerAccount(customer_id=customer.id, domain_id=domain.id, email_address=email, username=email)
        db.session.add(account)
        db.session.commit()
        logger.info("login account created request_id=%s account_id=%s", request_id, account.id)

    t3 = time.monotonic()
    derived_key = derive_key(password, email)
    account.auth_type = "password"
    if not account.cache_db_path:
        account.cache_db_path = build_cache_path(customer.id, account.id)

    user_key = _resolve_login_key(account, derived_key, password)

    account.encrypted_secret = encrypt_with_key(password, user_key)
    db.session.commit()
    logger.info(
        "login credentials stored request_id=%s account_id=%s elapsed_ms=%s",
        request_id,
        account.id,
        int((time.monotonic() - t3) * 1000),
    )

    set_user_key(customer.id, user_key)
    session["user_id"] = customer.id
    session["role"] = "customer"
    session["active_account_id"] = account.id
    session["user_key"] = user_key
    logger.info("login session set request_id=%s user_id=%s account_id=%s", request_id, customer.id, account.id)
    current_app.sync_manager.set_active_account(customer.id, account.id)
    current_app.sync_manager.set_active_folder(account.id, "INBOX")
    current_app.sync_manager.enqueue_sync(account.id, folder="INBOX", reason="login", priority=0)
    current_app.sync_manager.enqueue_sync(account.id, folder="Sent", reason="login", priority=5)
    logger.info("login redirect request_id=%s", request_id)
    if next_url and _is_safe_redirect_url(next_url):
        return redirect(next_url)
    return redirect(url_for("mail.folder_view", account_id=account.id, folder="INBOX"))


@mail_bp.route("/logout")
@require_customer
def logout():
    customer_id = session.get("user_id")
    clear_user_key(customer_id)
    current_app.sync_manager.clear_active_customer(customer_id)
    session.clear()
    return redirect(url_for("mail.login"))


@mail_bp.route("/auth/check")
def auth_check():
    if "user_id" in session and session.get("role") == "customer":
        return "", 200

    share_cookie = request.cookies.get("share_access")
    if share_cookie:
        from app.shared.models.core import DocShare
        share = DocShare.query.filter_by(
            share_token=share_cookie, revoked_at=None,
        ).first()
        if share:
            return "", 200

    return "", 401


@mail_bp.route("/api/set-timezone", methods=["POST"])
@require_customer
def set_timezone():
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    tz = request.get_json(silent=True)
    if not tz or "timezone" not in tz:
        return {"error": "missing timezone"}, 400
    tz_name = tz["timezone"].strip()
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return {"error": "invalid timezone"}, 400
    session["_browser_tz"] = tz_name
    return {"ok": True}
