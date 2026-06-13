from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.security import check_password_hash, generate_password_hash

from datetime import datetime, timedelta, timezone

from app.shared.models.core import User, Domain, CustomerAccount
from app.shared.db import db
from app.shared.audit import log_audit
from app.shared.rate_limit import record_failed_login, clear_failed_login, is_locked


def _dev_defaults():
    is_dev = current_app.config.get("APP_ENV") == "development"
    if is_dev:
        return dict(
            imap_host="dovecot", imap_port=143, imap_tls=False,
            smtp_host="postfix", smtp_port=587, smtp_tls_mode="starttls",
            carddav_host="radicale", carddav_port=5232, carddav_use_tls=False,
            caldav_host="radicale", caldav_port=5232, caldav_use_tls=False,
            mail_api_url=current_app.config.get("MAIL_API_URL", "") or None,
            mail_api_key=current_app.config.get("MAIL_API_KEY", "") or None,
        )
    return dict(
        imap_host="localhost", imap_port=993, imap_tls=True,
        smtp_host="localhost", smtp_port=587, smtp_tls_mode="starttls",
        carddav_host="localhost", carddav_port=5232, carddav_use_tls=False,
        caldav_host="localhost", caldav_port=5232, caldav_use_tls=False,
        mail_api_url=current_app.config.get("MAIL_API_URL", "") or None,
        mail_api_key=current_app.config.get("MAIL_API_KEY", "") or None,
    )


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/")
def index():
    return redirect(url_for("mail.login"))


@auth_bp.route("/login")
def legacy_login():
    return redirect(url_for("auth.login"))


@auth_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    title = "Domain Management Login"
    if request.method == "GET":
        role = session.get("role")
        if role == "admin":
            return redirect(url_for("admin.dashboard"))
        if role == "manager":
            return redirect(url_for("manager.dashboard"))
        return render_template("auth/login.html", title=title)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    ip = request.remote_addr
    user_agent = request.headers.get("User-Agent")

    if is_locked(email, ip):
        log_audit(actor_user_id=None, actor_role=None, action="login_failure", details=email, ip_address=ip, user_agent=user_agent)
        return render_template("auth/login.html", error="Account temporarily locked.", title=title)

    user = User.query.filter_by(email=email).first()
    if not user or user.role not in ("admin", "manager"):
        record_failed_login(email, ip)
        log_audit(actor_user_id=None, actor_role=None, action="login_failure", details=email, ip_address=ip, user_agent=user_agent)
        return render_template("auth/login.html", error="Invalid credentials.", title=title)

    if not user.is_active:
        return render_template("auth/login.html", error="Account deactivated.", title=title)

    if not user.password_hash or not check_password_hash(user.password_hash, password):
        record_failed_login(email, ip)
        log_audit(actor_user_id=None, actor_role=None, action="login_failure", details=email, ip_address=ip, user_agent=user_agent)
        return render_template("auth/login.html", error="Invalid credentials.", title=title)

    clear_failed_login(email, ip)
    session["user_id"] = user.id
    session["role"] = user.role

    log_audit(
        actor_user_id=user.id,
        actor_role=user.role,
        action="login_success",
        details="",
        ip_address=ip,
        user_agent=user_agent,
    )

    if user.role == "admin":
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("manager.dashboard"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/admin/setup", methods=["GET", "POST"])
def setup():
    if User.query.filter_by(role="admin").first() is not None:
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("auth/setup.html", title="Welcome to LocoRooSuite")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")
    domain_name = request.form.get("domain", "test.localhost").strip().lower()

    errors = []
    if not email:
        errors.append("Email is required.")
    if not password:
        errors.append("Password is required.")
    if password and password != password_confirm:
        errors.append("Passwords do not match.")
    if not domain_name:
        errors.append("Domain is required.")

    if errors:
        return render_template("auth/setup.html", title="Welcome to LocoRooSuite", errors=errors, email=email, domain=domain_name)

    admin = User(role="admin", email=email, password_hash=generate_password_hash(password))
    db.session.add(admin)
    db.session.flush()

    defaults = _dev_defaults()
    domain = Domain(
        name=domain_name,
        imap_host=defaults["imap_host"],
        imap_port=defaults["imap_port"],
        imap_tls=defaults["imap_tls"],
        smtp_host=defaults["smtp_host"],
        smtp_port=defaults["smtp_port"],
        smtp_tls_mode=defaults["smtp_tls_mode"],
        carddav_host=defaults["carddav_host"],
        carddav_port=defaults["carddav_port"],
        carddav_use_tls=defaults["carddav_use_tls"],
        caldav_host=defaults["caldav_host"],
        caldav_port=defaults["caldav_port"],
        caldav_use_tls=defaults["caldav_use_tls"],
        mail_api_url=defaults.get("mail_api_url"),
        mail_api_key=defaults.get("mail_api_key"),
        status="complete",
    )
    db.session.add(domain)
    db.session.commit()

    log_audit(admin.id, "admin", "setup_complete", f"email={email},domain={domain_name}", request.remote_addr, request.headers.get("User-Agent"))

    _sync_setup_domain_to_mail_api(domain)

    session["user_id"] = admin.id
    session["role"] = "admin"
    session["just_completed_setup"] = True
    session.permanent = True

    return redirect(url_for("admin.dashboard"))


def _sync_setup_domain_to_mail_api(domain):
    from app.admin.services.mail_server import get_mail_client_for_domain
    client = get_mail_client_for_domain(domain)
    if client is None:
        return
    try:
        client.add_domain(domain.name)
    except Exception:
        pass


@auth_bp.route("/signup/<token>", methods=["GET", "POST"])
def signup(token):
    account = CustomerAccount.query.filter_by(signup_token=token).first()
    if not account:
        return render_template("auth/signup.html", title="Sign Up", error="This invitation link is invalid or has expired."), 404

    if account.signup_expires_at:
        expires = account.signup_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return render_template("auth/signup.html", title="Sign Up", token=token, error="This invitation link has expired."), 410

    if account.encrypted_secret is not None:
        return redirect(url_for("mail.login"))

    customer = db.session.get(User, account.customer_id)
    domain = db.session.get(Domain, account.domain_id)

    if request.method == "GET":
        return render_template("auth/signup.html", title="Sign Up", token=token, email=account.email_address, domain=domain.name if domain else "")

    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    if not password:
        return render_template("auth/signup.html", title="Sign Up", token=token, email=account.email_address, error="Password is required.")
    if password != password_confirm:
        return render_template("auth/signup.html", title="Sign Up", token=token, email=account.email_address, error="Passwords do not match.")

    from app.admin.controllers.admin import _mail_api_call
    domain = db.session.get(Domain, account.domain_id)
    _mail_api_call(
        "create_mailbox",
        lambda c: c.add_user(account.email_address, password),
        domain=domain,
    )

    account.signup_token = None
    account.signup_expires_at = None
    db.session.commit()

    log_audit(customer.id, "customer", "signup_complete", account.email_address, request.remote_addr, request.headers.get("User-Agent"))
    flash("Account created. You can now log in.", "success")
    return redirect(url_for("mail.login"))


@auth_bp.before_app_request
def _redirect_to_setup():
    if request.path == url_for("auth.setup"):
        return None
    if request.path.startswith("/admin") or request.path == url_for("auth.login"):
        if User.query.filter_by(role="admin").first() is None:
            return redirect(url_for("auth.setup"))
    return None
