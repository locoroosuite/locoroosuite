from flask import Flask, session, url_for, redirect, render_template, request

from app.config import AppConfig
from app.shared.db import db
from app.shared.models import core
from app.shared.models import imports as import_models
from app.shared.models import oauth as oauth_models
from app.shared.logging import configure_logging
from app.shared.keys import get_user_key
from app.shared.db_migrations import (
    ensure_domain_status_column,
    ensure_customer_settings_spam_action_column,
    ensure_import_request_takeout_columns,
    ensure_domain_carddav_columns,
    ensure_domain_caldav_columns,
    ensure_api_columns,
    ensure_oauth_token_dek,
    ensure_customer_signup_columns,
    ensure_domain_mail_api_columns,
    ensure_domain_dns_config_columns,
    ensure_user_totp_columns,
)

import logging
import os
import re
from datetime import timedelta

_logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(AppConfig)
    configure_logging(app)

    db.init_app(app)

    from app.modules.mail import register as register_mail
    from app.modules.contacts import register as register_contacts
    from app.modules.calendar import register as register_calendar
    from app.modules.docs import register as register_docs
    from app.admin import register as register_admin
    from app.api import register as register_api
    from app.shared.oauth import register as register_oauth
    from app.provisioning import register as register_provisioning

    register_mail(app)
    register_contacts(app)
    register_calendar(app)
    register_docs(app)
    register_admin(app)
    register_api(app)
    register_oauth(app)
    register_provisioning(app)

    @app.before_request
    def _set_session_lifetime():
        role = session.get("role")
        if role == "customer":
            app.permanent_session_lifetime = timedelta(days=30)
            session.permanent = True
        elif role in ("admin", "manager"):
            app.permanent_session_lifetime = timedelta(minutes=30)
            session.permanent = True

    def _initials(email):
        if not email:
            return "U"
        local = email.split("@")[0]
        parts = [part for part in re.split(r"[._\\s-]+", local) if part]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return local[:2].upper() if len(local) >= 2 else local[:1].upper()

    @app.context_processor
    def inject_user_menu():
        if request.path == "/app/auth/check":
            return {"user_menu": None}
        user_id = session.get("user_id")
        role = session.get("role")
        if not user_id:
            return {"user_menu": None}
        user = db.session.get(core.User, user_id)
        email = user.email if user else ""
        show_domain = False
        if email:
            show_domain = (
                core.User.query.filter(
                    core.User.email == email,
                    core.User.role.in_(("admin", "manager")),
                    core.User.is_active.is_(True),
                ).first()
                is not None
            )
        settings_url = url_for("mail.settings") if role == "customer" else None
        logout_url = url_for("mail.logout") if role == "customer" else url_for("auth.logout")
        customer_accounts = []
        active_account_id = session.get("active_account_id")
        if role == "customer" and user_id:
            customer_accounts = core.CustomerAccount.query.filter_by(
                customer_id=user_id, is_active=True
            ).all()
        return {
            "user_menu": {
                "email": email,
                "initials": _initials(email),
                "settings_url": settings_url,
                "logout_url": logout_url,
                "domain_url": url_for("auth.login") if show_domain else None,
                "show_mail": role == "customer",
                "show_contacts": role == "customer",
                "show_calendar": role == "customer",
                "show_docs": role == "customer",
                "show_admin": role in ("admin", "manager"),
            },
            "accounts": customer_accounts,
            "active_account_id": active_account_id,
        }

    @app.before_request
    def _validate_session_key():
        if request.path.startswith("/api/"):
            return None
        role = session.get("role")
        user_id = session.get("user_id")
        if role != "customer" or not user_id:
            return None
        key = get_user_key(user_id)
        if key is not None:
            return None
        _logger.info("session key missing for user %s, redirecting to login", user_id)
        session.clear()
        if not request.accept_mimetypes.accept_html:
            return {"error": {"code": "SESSION_EXPIRED", "message": "Your session has expired. Please log in again."}}, 401
        login_url = url_for("mail.login")
        if request.path != url_for("mail.login"):
            login_url = url_for("mail.login", next=request.url)
        return redirect(login_url)

    @app.errorhandler(404)
    def _handle_404(exc):
        if request.accept_mimetypes.accept_html:
            return render_template("error.html", title="Not Found", message="The page you requested does not exist."), 404
        return {"error": {"code": "NOT_FOUND", "message": "The requested resource does not exist."}}, 404

    @app.errorhandler(405)
    def _handle_405(exc):
        if request.accept_mimetypes.accept_html:
            return render_template("error.html", title="Method Not Allowed", message="This action is not supported."), 405
        return {"error": {"code": "METHOD_NOT_ALLOWED", "message": "This HTTP method is not supported for the requested endpoint."}}, 405

    from app.shared.cache_errors import CacheKeyMismatchError

    @app.errorhandler(CacheKeyMismatchError)
    def _handle_cache_key_mismatch(exc):
        _logger.warning("cache key mismatch on %s %s", request.method, request.path)
        account_id = session.get("active_account_id")
        if request.accept_mimetypes.accept_html:
            return render_template(
                "error.html",
                title="Cache key mismatch",
                message="Your local mail cache was encrypted with a different key. "
                "This can happen if your password was changed or your API access was reconfigured. "
                "Please reset your cache to continue — your mail will be re-synced from the server.",
                show_cache_reset=True,
                account_id=account_id,
            ), 500
        return {"error": {
            "code": "CACHE_KEY_MISMATCH",
            "message": "Your encrypted cache cannot be opened because the encryption key does not match. "
            "This can happen after a password change, a server upgrade, or an API key rotation. "
            "To fix this: go to Settings → API → Disable API access, then re-enable it and create a new API token. "
            "This resets your encryption keys and re-syncs your data from the mail server.",
            "account_id": account_id,
        }}, 500

    @app.errorhandler(Exception)
    def _handle_exception(exc):
        _logger.exception("unhandled exception on %s %s", request.method, request.path)
        if request.accept_mimetypes.accept_html:
            return render_template(
                "error.html",
                title="Something went wrong",
                message="An unexpected error occurred. Please try again or refresh the page.",
            ), 500
        return {"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred. Please retry or contact support."}}, 500

    with app.app_context():
        db.create_all()
        ensure_domain_status_column()
        ensure_customer_settings_spam_action_column()
        ensure_import_request_takeout_columns()
        ensure_domain_carddav_columns()
        ensure_domain_caldav_columns()
        ensure_api_columns()
        ensure_oauth_token_dek()
        ensure_customer_signup_columns()
        ensure_domain_mail_api_columns()
        ensure_domain_dns_config_columns()
        ensure_user_totp_columns()

    from app.workers.manager import WorkerManager
    worker = WorkerManager(app)
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        worker.start()
    app.sync_manager = worker

    from app.shared.cli import register_cli
    register_cli(app)

    return app
