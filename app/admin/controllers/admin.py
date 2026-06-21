from datetime import datetime, timedelta, timezone
import logging
import secrets

from flask import current_app, jsonify, render_template, request, redirect, url_for, session, flash
from markupsafe import Markup
from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import User, Domain, ManagerDomain, CustomerAccount, PlatformDnsConfig, PlatformServiceConfig, DomainDnsConfig
from app.shared.models.imports import ImportRequest, ImportRun
from app.admin.services.domain_discovery import discover_domain_settings
from app.admin.services.import_security import build_import_token, encrypt_import_secret, new_link_key
from app.shared.audit import log_audit
from app.shared.auth import require_role
from app.modules.mail.services.cache import purge_cache
from app.admin import admin_bp

logger = logging.getLogger(__name__)


def _customer_accounts_map(customer_ids: list[int] | None = None) -> dict[int, list[CustomerAccount]]:
    query = CustomerAccount.query
    if customer_ids is not None:
        query = query.filter(CustomerAccount.customer_id.in_(customer_ids))
    result: dict[int, list[CustomerAccount]] = {}
    for acc in query.all():
        result.setdefault(acc.customer_id, []).append(acc)
    return result


@admin_bp.route("/")
@require_role("admin")
def dashboard():
    domains = Domain.query.all()
    managers = User.query.filter_by(role="manager").all()
    customers = User.query.filter_by(role="customer").all()
    import_requests = ImportRequest.query.all()
    show_setup_banner = bool(session.get("just_completed_setup"))
    admin_user = db.session.get(User, session.get("user_id")) if show_setup_banner else None
    return render_template(
        "admin/overview.html",
        domains=domains,
        managers=managers,
        customers=customers,
        import_requests=import_requests,
        active_page="overview",
        title="Admin Overview",
        show_setup_banner=show_setup_banner,
        setup_admin_email=admin_user.email if admin_user else "",
    )


@admin_bp.route("/dismiss-setup-banner", methods=["POST"])
@require_role("admin")
def dismiss_setup_banner():
    session.pop("just_completed_setup", None)
    return jsonify(ok=True)


@admin_bp.route("/domains")
@require_role("admin")
def domains():
    domains = Domain.query.all()
    dns_configs: dict[int, DomainDnsConfig] = {
        c.domain_id: c for c in DomainDnsConfig.query.filter(DomainDnsConfig.domain_id.in_([d.id for d in domains])).all()
    }
    return render_template(
        "admin/domains.html",
        domains=domains,
        dns_configs=dns_configs,
        app_env=current_app.config.get("APP_ENV", "production"),
        active_page="domains",
        title="Admin Domains",
    )


@admin_bp.route("/managers")
@require_role("admin")
def managers():
    managers = User.query.filter_by(role="manager").all()
    return render_template(
        "admin/managers.html",
        managers=managers,
        active_page="managers",
        title="Admin Managers",
    )


@admin_bp.route("/assignments")
@require_role("admin")
def assignments():
    domains = Domain.query.all()
    managers = User.query.filter_by(role="manager").all()
    assignments = (
        db.session.query(ManagerDomain, User, Domain)
        .join(User, User.id == ManagerDomain.manager_id)
        .join(Domain, Domain.id == ManagerDomain.domain_id)
        .order_by(Domain.name, User.email)
        .all()
    )
    return render_template(
        "admin/assignments.html",
        domains=domains,
        managers=managers,
        assignments=assignments,
        active_page="assignments",
        title="Admin Assignments",
    )


@admin_bp.route("/customers")
@require_role("admin")
def customers():
    customers = User.query.filter_by(role="customer").all()
    domains = Domain.query.all()
    customer_accounts = _customer_accounts_map([c.id for c in customers])

    admin_user = db.session.get(User, session.get("user_id"))
    admin_account = None
    if admin_user and admin_user.role == "admin":
        admin_account = CustomerAccount.query.filter_by(customer_id=admin_user.id).first()
        if not admin_account:
            email_domain = admin_user.email.split("@")[-1] if admin_user.email else ""
            domain = Domain.query.filter(Domain.name == email_domain).first()
            if domain:
                admin_account = CustomerAccount(
                    customer_id=admin_user.id,
                    domain_id=domain.id,
                    email_address=admin_user.email,
                    auth_type="password",
                    username=admin_user.email.split("@")[0],
                )
                db.session.add(admin_account)
                db.session.commit()

    return render_template(
        "admin/customers.html",
        customers=customers,
        customer_accounts=customer_accounts,
        domains=domains,
        admin_user=admin_user,
        admin_account=admin_account,
        sync_domains=_sync_domains(domains),
        active_page="customers",
        title="Admin Customers",
    )


@admin_bp.route("/imports")
@require_role("admin")
def imports():
    import_requests = ImportRequest.query.order_by(ImportRequest.created_at.desc(), ImportRequest.id.desc()).all()
    google_ready = bool(
        current_app.config.get("GOOGLE_IMPORT_CLIENT_ID")
        and current_app.config.get("GOOGLE_IMPORT_CLIENT_SECRET")
    )
    latest_runs = {}
    for row in import_requests:
        latest_runs[row.id] = (
            ImportRun.query.filter_by(import_request_id=row.id)
            .order_by(ImportRun.started_at.desc(), ImportRun.id.desc())
            .first()
        )
    return render_template(
        "admin/imports.html",
        import_requests=import_requests,
        latest_runs=latest_runs,
        google_ready=google_ready,
        active_page="imports",
        title="Admin Imports",
    )


@admin_bp.route("/domains/new", methods=["POST"])
@require_role("admin")
def create_domain():
    name = request.form.get("name", "").strip().lower()
    if not name:
        return redirect(url_for("admin.domains"))
    global_mail_url = current_app.config.get("MAIL_API_URL", "")
    global_mail_key = current_app.config.get("MAIL_API_KEY", "")
    domain = Domain(
        name=name,
        imap_host="",
        imap_port=993,
        imap_tls=True,
        smtp_host="",
        smtp_port=587,
        smtp_tls_mode="starttls",
        mail_api_url=global_mail_url or None,
        mail_api_key=global_mail_key or None,
        status="draft",
    )

    if current_app.config.get("APP_ENV") == "development" and name.endswith(".localhost"):
        domain.imap_host = "dovecot"
        domain.imap_port = 143
        domain.imap_tls = False
        domain.smtp_host = "postfix"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        domain.carddav_host = "radicale"
        domain.carddav_port = 5232
        domain.carddav_use_tls = False
        domain.caldav_host = "radicale"
        domain.caldav_port = 5232
        domain.caldav_use_tls = False
        domain.status = "complete"

    db.session.add(domain)
    db.session.commit()

    is_dev_localhost = current_app.config.get("APP_ENV") == "development" and name.endswith(".localhost")
    if not is_dev_localhost:
        discovery = discover_domain_settings(name)
        _apply_discovery(domain, discovery)
        db.session.commit()
    else:
        discovery = {"imap_primary": None, "smtp_primary": None, "imap_candidates": [], "smtp_candidates": []}

    log_audit(session.get("user_id"), "admin", "domain_create", name, request.remote_addr, request.headers.get("User-Agent"))
    _sync_domain_to_mail_api(domain)
    if not is_dev_localhost and _needs_review(discovery, domain):
        return redirect(url_for("admin.review_domain_mail", domain_id=domain.id))
    return redirect(url_for("admin.domains"))


@admin_bp.route("/domains/<int:domain_id>/review", methods=["GET", "POST"])
@require_role("admin")
def review_domain(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    if request.method == "POST":
        _apply_domain_form(domain, request.form)
        domain.status = _compute_domain_status(domain)
        db.session.commit()
        log_audit(
            session.get("user_id"),
            "admin",
            "domain_update",
            domain.name,
            request.remote_addr,
            request.headers.get("User-Agent"),
        )
        _sync_domain_to_mail_api(domain)
        return redirect(url_for("admin.domains"))
    return redirect(url_for("admin.review_domain_mail", domain_id=domain_id))


@admin_bp.route("/domains/<int:domain_id>/review/mail")
@require_role("admin")
def review_domain_mail(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    discovery = discover_domain_settings(domain.name)
    missing = _missing_domain_fields(domain)
    return render_template(
        "admin/domain_review_mail.html",
        domain=domain,
        discovery=discovery,
        missing=missing,
        active_page="domains",
        active_tab="mail",
        title=f"Review {domain.name}",
    )


@admin_bp.route("/domains/<int:domain_id>/review/dav")
@require_role("admin")
def review_domain_dav(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    return render_template(
        "admin/domain_review_dav.html",
        domain=domain,
        active_page="domains",
        active_tab="dav",
        title=f"Review {domain.name}",
    )


@admin_bp.route("/domains/<int:domain_id>/review/self-hosted")
@require_role("admin")
def review_domain_selfhosted(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    dns_config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    platform_dns = PlatformDnsConfig.query.order_by(PlatformDnsConfig.mx_priority).all()
    return render_template(
        "admin/domain_review_selfhosted.html",
        domain=domain,
        dns_config=dns_config,
        platform_dns=platform_dns,
        active_page="domains",
        active_tab="selfhosted",
        title=f"Review {domain.name}",
    )


@admin_bp.route("/domains/<int:domain_id>/review/accounts")
@require_role("admin")
def review_domain_accounts(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    accounts = (
        CustomerAccount.query
        .filter_by(domain_id=domain_id)
        .all()
    )
    dns_config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    return render_template(
        "admin/domain_review_accounts.html",
        domain=domain,
        accounts=accounts,
        dns_config=dns_config,
        active_page="domains",
        active_tab="accounts",
        title=f"Review {domain.name}",
    )


@admin_bp.route("/managers/new", methods=["POST"])
@require_role("admin")
def create_manager():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    manager = User(role="manager", email=email, password_hash=generate_password_hash(password))
    db.session.add(manager)
    db.session.commit()

    log_audit(session.get("user_id"), "admin", "manager_create", email, request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("admin.managers"))


@admin_bp.route("/customers/new", methods=["POST"])
@require_role("admin")
def create_customer():
    username = request.form.get("username", "").strip().lower()
    domain_id = request.form.get("domain_id", "")
    password = request.form.get("password", "")
    create_mode = request.form.get("create_mode", "invite")

    if not username:
        flash("Username is required.", "error")
        return redirect(url_for("admin.customers"))

    if not domain_id:
        flash("Select a domain for the customer.", "error")
        return redirect(url_for("admin.customers"))

    domain = db.session.get(Domain, int(domain_id))
    if not domain:
        flash("Domain not found.", "error")
        return redirect(url_for("admin.customers"))

    email = f"{username}@{domain.name}"

    existing = User.query.filter_by(email=email).first()
    if existing:
        _flash_user_exists(email, domain)
        return redirect(url_for("admin.customers"))

    if create_mode == "invite":
        from app.admin.services.mail_server import get_mail_client_for_domain
        client = get_mail_client_for_domain(domain)
        if client and client.check_user(email):
            _flash_mailbox_exists(email, domain)
            return redirect(url_for("admin.customers"))

    customer = User(role="customer", email=email)
    db.session.add(customer)
    db.session.flush()

    if create_mode == "password" and password:
        account = CustomerAccount(
            customer_id=customer.id,
            domain_id=domain.id,
            email_address=email,
            auth_type="password",
            username=email,
        )
        db.session.add(account)
        db.session.flush()

        from app.admin.services.mail_server import get_mail_client_for_domain
        client = get_mail_client_for_domain(domain)
        if client:
            try:
                client.add_user(email, password)
            except Exception as exc:
                db.session.rollback()
                logger.error("mail-api create_mailbox failed: %s", exc)
                _flash_mailbox_exists(email, domain, exc)
                return redirect(url_for("admin.customers"))

        db.session.commit()
        log_audit(session.get("user_id"), "admin", "customer_create", f"email={email},mode=password", request.remote_addr, request.headers.get("User-Agent"))
        flash(Markup(f'Customer {email} created with password. To log in as a customer, <a href="{url_for("mail.login")}" class="underline font-medium">click here</a>.'), "success")
        return redirect(url_for("admin.customers"))

    if create_mode == "external":
        account = CustomerAccount(
            customer_id=customer.id,
            domain_id=domain.id,
            email_address=email,
            auth_type="external",
            username=email,
        )
        db.session.add(account)
        db.session.commit()
        log_audit(session.get("user_id"), "admin", "customer_create", f"email={email},mode=external", request.remote_addr, request.headers.get("User-Agent"))
        flash(f"Customer {email} created as external mailbox.", "success")
        return redirect(url_for("admin.customers"))

    token = secrets.token_urlsafe(32)
    account = CustomerAccount(
        customer_id=customer.id,
        domain_id=domain.id,
        email_address=email,
        auth_type="password",
        username=email,
        signup_token=token,
        signup_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.session.add(account)
    db.session.commit()

    signup_url = f"{current_app.config['APP_URL'].rstrip('/')}/signup/{token}"
    log_audit(session.get("user_id"), "admin", "customer_create", f"email={email},mode=invite", request.remote_addr, request.headers.get("User-Agent"))
    all_customers = User.query.filter_by(role="customer").all()
    return render_template(
        "admin/customers.html",
        customers=all_customers,
        customer_accounts=_customer_accounts_map([c.id for c in all_customers]),
        domains=Domain.query.all(),
        sync_domains=_sync_domains(),
        active_page="customers",
        title="Admin Customers",
        invitation_link=signup_url,
        invitation_email=email,
    )


@admin_bp.route("/imports/new", methods=["POST"])
@require_role("admin")
def create_import_request():
    customer_email = request.form.get("customer_email", "").strip().lower()
    source_type = request.form.get("source_type", "google").strip().lower()
    destination_email = request.form.get("destination_email", "").strip().lower()
    destination_imap_host = request.form.get("destination_imap_host", "").strip()
    destination_username = request.form.get("destination_username", "").strip()
    destination_secret = request.form.get("destination_password", "")
    expiry_days = _parse_int(request.form.get("expiry_days"), 7)
    destination_imap_port = _parse_int(request.form.get("destination_imap_port"), 993)

    if source_type not in {"google", "google_takeout"}:
        return redirect(url_for("admin.imports"))
    if not all((customer_email, destination_email, destination_imap_host, destination_username, destination_secret)):
        return redirect(url_for("admin.imports"))

    import_request = ImportRequest(
        created_by_user_id=session.get("user_id"),
        customer_email=customer_email,
        source_type=source_type,
        destination_email=destination_email,
        destination_imap_host=destination_imap_host,
        destination_imap_port=destination_imap_port,
        destination_imap_tls=True,
        destination_username=destination_username,
        encrypted_destination_secret=b"pending",
        link_key=new_link_key(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=max(1, min(expiry_days, 90))),
        status="pending_auth" if source_type == "google" else "pending_upload",
        upload_status="none" if source_type == "google" else "pending_upload",
    )
    db.session.add(import_request)
    db.session.commit()

    import_request.encrypted_destination_secret = encrypt_import_secret(
        import_request.id,
        "destination_secret",
        destination_secret,
    )
    db.session.commit()
    log_audit(
        session.get("user_id"),
        "admin",
        "import_request_create",
        f"import_request={import_request.id}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    return redirect(url_for("admin.imports"))


@admin_bp.route("/imports/<int:import_request_id>/toggle", methods=["POST"])
@require_role("admin")
def toggle_import_request(import_request_id):
    import_request = db.get_or_404(ImportRequest, import_request_id)
    import_request.is_enabled = not import_request.is_enabled
    if not import_request.is_enabled:
        import_request.status = "disabled"
    elif import_request.encrypted_source_refresh_token:
        import_request.status = "ready"
    else:
        import_request.status = "pending_auth"
    db.session.commit()
    log_audit(
        session.get("user_id"),
        "admin",
        "import_request_toggle",
        f"import_request={import_request.id},enabled={int(import_request.is_enabled)}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    return redirect(url_for("admin.imports"))


@admin_bp.route("/assign-manager", methods=["POST"])
@require_role("admin")
def assign_manager():
    manager_id = int(request.form.get("manager_id"))
    domain_id = int(request.form.get("domain_id"))
    link = ManagerDomain(manager_id=manager_id, domain_id=domain_id)
    db.session.add(link)
    db.session.commit()

    log_audit(session.get("user_id"), "admin", "manager_assign", f"manager={manager_id},domain={domain_id}", request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("admin.assignments"))


@admin_bp.route("/domains/<int:domain_id>/toggle", methods=["POST"])
@require_role("admin")
def toggle_domain(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    domain.is_active = not domain.is_active
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "domain_toggle", f"domain={domain.name},active={domain.is_active}", request.remote_addr, request.headers.get("User-Agent"))
    _sync_domain_to_mail_api(domain)
    return redirect(url_for("admin.domains"))


@admin_bp.route("/domains/<int:domain_id>/delete", methods=["POST"])
@require_role("admin")
def delete_domain(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    domain_name = domain.name

    _mail_api_call("remove_domain", lambda c: c.remove_domain(domain_name), domain=domain)

    accounts = CustomerAccount.query.filter_by(domain_id=domain_id).all()
    for account in accounts:
        user = db.session.get(User, account.customer_id)
        if user and user.role == "customer":
            db.session.delete(user)
        db.session.delete(account)

    DomainDnsConfig.query.filter_by(domain_id=domain_id).delete()
    ManagerDomain.query.filter_by(domain_id=domain_id).delete()

    db.session.delete(domain)
    db.session.commit()

    log_audit(session.get("user_id"), "admin", "domain_delete", f"domain={domain_name}", request.remote_addr, request.headers.get("User-Agent"))
    flash(f"Domain {domain_name} deleted.", "success")
    return redirect(url_for("admin.domains"))


@admin_bp.route("/domains/<int:domain_id>/update", methods=["POST"])
@require_role("admin")
def update_domain(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    domain.imap_host = request.form.get("imap_host", domain.imap_host)
    domain.imap_port = _parse_int(request.form.get("imap_port"), domain.imap_port)
    domain.smtp_host = request.form.get("smtp_host", domain.smtp_host)
    domain.smtp_port = _parse_int(request.form.get("smtp_port"), domain.smtp_port)
    domain.smtp_tls_mode = request.form.get("smtp_tls_mode", domain.smtp_tls_mode)
    domain.imap_auth_methods = request.form.get("imap_auth_methods", domain.imap_auth_methods)
    domain.smtp_auth_methods = request.form.get("smtp_auth_methods", domain.smtp_auth_methods)
    domain.caldav_host = request.form.get("caldav_host", domain.caldav_host) or None
    domain.caldav_port = _parse_int(request.form.get("caldav_port"), domain.caldav_port)
    domain.caldav_use_tls = request.form.get("caldav_use_tls") == "1"
    domain.carddav_host = request.form.get("carddav_host", domain.carddav_host) or None
    domain.carddav_port = _parse_int(request.form.get("carddav_port"), domain.carddav_port)
    domain.carddav_use_tls = request.form.get("carddav_use_tls") == "1"
    domain.mail_api_url = request.form.get("mail_api_url", "").strip() or None
    domain.mail_api_key = request.form.get("mail_api_key", "").strip() or None
    domain.status = _compute_domain_status(domain)
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "domain_update", domain.name, request.remote_addr, request.headers.get("User-Agent"))
    _sync_domain_to_mail_api(domain)
    return redirect(url_for("admin.domains"))


@admin_bp.route("/domains/<int:domain_id>/managers/<int:manager_id>/remove", methods=["POST"])
@require_role("admin")
def unassign_manager(domain_id, manager_id):
    link = ManagerDomain.query.filter_by(domain_id=domain_id, manager_id=manager_id).first()
    if link:
        db.session.delete(link)
        db.session.commit()
    log_audit(session.get("user_id"), "admin", "manager_unassign", f"manager={manager_id},domain={domain_id}", request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("admin.assignments"))


@admin_bp.route("/customers/<int:customer_id>/toggle", methods=["POST"])
@require_role("admin")
def toggle_customer(customer_id):
    customer = db.get_or_404(User, customer_id)
    customer.is_active = not customer.is_active
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "customer_toggle", f"customer={customer.email},active={customer.is_active}", request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("admin.customers"))


@admin_bp.route("/customers/<int:customer_id>/purge", methods=["POST"])
@require_role("admin")
def purge_customer(customer_id):
    accounts = CustomerAccount.query.filter_by(customer_id=customer_id).all()
    for account in accounts:
        purge_cache(account.cache_db_path)
        account.cache_db_path = None
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "customer_cache_reset", f"customer={customer_id}", request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("admin.customers"))


@admin_bp.route("/customers/<int:customer_id>/add-account", methods=["POST"])
@require_role("admin")
def add_customer_account(customer_id):
    customer = db.get_or_404(User, customer_id)
    domain_id = request.form.get("domain_id", "").strip()
    password = request.form.get("password", "")

    if not domain_id:
        flash("Select a domain.", "error")
        return redirect(url_for("admin.customers"))

    domain = db.session.get(Domain, int(domain_id))
    if not domain:
        flash("Domain not found.", "error")
        return redirect(url_for("admin.customers"))

    email = f"{customer.email.split('@')[0]}@{domain.name}"

    existing = CustomerAccount.query.filter_by(email_address=email).first()
    if existing:
        flash(f"Account {email} already exists.", "error")
        return redirect(url_for("admin.customers"))

    account = CustomerAccount(
        customer_id=customer.id,
        domain_id=domain.id,
        email_address=email,
        auth_type="password",
        username=email,
    )
    db.session.add(account)
    db.session.flush()

    _mail_api_call(
        "create_mailbox",
        lambda c: _upsert_user(c, email, password) if password else c.add_user(email, password or secrets.token_urlsafe(16)),
        domain=domain,
    )
    db.session.commit()

    log_audit(
        session.get("user_id"),
        "admin",
        "customer_add_account",
        f"customer={customer.email},account={email}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    flash(f"Account {email} added for {customer.email}.", "success")
    return redirect(url_for("admin.customers"))


@admin_bp.route("/customers/<int:customer_id>/reset-password", methods=["POST"])
@require_role("admin")
def reset_customer_password(customer_id):
    customer = db.get_or_404(User, customer_id)
    account = CustomerAccount.query.filter_by(customer_id=customer_id).first()
    if not account:
        flash("No account found for this customer.", "error")
        return redirect(url_for("admin.customers"))

    token = secrets.token_urlsafe(32)
    account.signup_token = token
    account.signup_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.session.commit()

    signup_url = f"{current_app.config['APP_URL'].rstrip('/')}/signup/{token}"
    log_audit(session.get("user_id"), "admin", "customer_password_reset", f"email={customer.email}", request.remote_addr, request.headers.get("User-Agent"))
    all_customers = User.query.filter_by(role="customer").all()
    return render_template(
        "admin/customers.html",
        customers=all_customers,
        customer_accounts=_customer_accounts_map([c.id for c in all_customers]),
        domains=Domain.query.all(),
        active_page="customers",
        title="Admin Customers",
        invitation_link=signup_url,
        invitation_email=customer.email,
    )


@admin_bp.route("/customers/<int:customer_id>/set-password", methods=["POST"])
@require_role("admin")
def set_customer_password(customer_id):
    customer = db.get_or_404(User, customer_id)
    password = request.form.get("password", "").strip()
    if not password:
        flash("Password is required.", "error")
        return redirect(url_for("admin.customers"))

    account = CustomerAccount.query.filter_by(customer_id=customer_id).first()
    if not account:
        flash("No account found for this customer.", "error")
        return redirect(url_for("admin.customers"))

    account.auth_type = "password"
    account.signup_token = None
    account.signup_expires_at = None
    if account.cache_db_path:
        purge_cache(account.cache_db_path)
        account.cache_db_path = None
    account.dek_wrapped_cred = None
    db.session.commit()

    domain = db.session.get(Domain, account.domain_id)
    _mail_api_call(
        "update_mailbox",
        lambda c: _upsert_user(c, account.email_address, password),
        domain=domain,
    )

    log_audit(session.get("user_id"), "admin", "customer_password_set", f"email={customer.email}", request.remote_addr, request.headers.get("User-Agent"))
    flash(f"Password updated for {customer.email}.", "success")
    return redirect(url_for("admin.customers"))


@admin_bp.route("/customers/<int:customer_id>/toggle-external", methods=["POST"])
@require_role("admin")
def toggle_customer_external(customer_id):
    customer = db.get_or_404(User, customer_id)
    account = CustomerAccount.query.filter_by(customer_id=customer_id).first()
    mode = request.form.get("mode", "external")

    if mode == "external":
        if account:
            account.auth_type = "external"
            account.signup_token = None
            account.signup_expires_at = None
        else:
            domain = Domain.query.first()
            if not domain:
                flash("No domain found. Create a domain first.", "error")
                return redirect(url_for("admin.customers"))
            account = CustomerAccount(
                customer_id=customer.id,
                domain_id=domain.id,
                email_address=customer.email,
                auth_type="external",
                username=customer.email,
            )
            db.session.add(account)
        label = "external"
    else:
        if not account:
            flash("No account found for this customer.", "error")
            return redirect(url_for("admin.customers"))
        account.auth_type = "password"
        label = "hosted"

    db.session.commit()
    log_audit(session.get("user_id"), "admin", "customer_toggle_external", f"email={customer.email},mode={label}", request.remote_addr, request.headers.get("User-Agent"))
    flash(f"{customer.email} is now {label}.", "success")
    return redirect(url_for("admin.customers"))


@admin_bp.route("/managers/<int:manager_id>/reset", methods=["POST"])
@require_role("admin")
def reset_manager_password(manager_id):
    manager = db.get_or_404(User, manager_id)
    password = request.form.get("password", "")
    manager.password_hash = generate_password_hash(password)
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "manager_password_reset", f"manager={manager.email}", request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("admin.managers"))


@admin_bp.route("/api/domains/health", methods=["GET"])
@require_role("admin")
def domains_health():
    from app.admin.services.health_checks import check_domain_services

    domains = Domain.query.all()
    result = {}
    for d in domains:
        result[d.id] = check_domain_services(d)
    return jsonify(result), 200


@admin_bp.route("/domains/<int:domain_id>/mail-config", methods=["POST"])
@require_role("admin")
def save_mail_config(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    domain.imap_host = request.form.get("imap_host", "").strip() or domain.imap_host
    domain.imap_port = _parse_int(request.form.get("imap_port"), domain.imap_port)
    domain.imap_auth_methods = request.form.get("imap_auth_methods", "").strip() or None
    domain.smtp_host = request.form.get("smtp_host", "").strip() or domain.smtp_host
    domain.smtp_port = _parse_int(request.form.get("smtp_port"), domain.smtp_port)
    domain.smtp_tls_mode = request.form.get("smtp_tls_mode", "").strip() or domain.smtp_tls_mode
    domain.smtp_auth_methods = request.form.get("smtp_auth_methods", "").strip() or None
    domain.status = _compute_domain_status(domain)
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "domain_mail_config", domain.name, request.remote_addr, request.headers.get("User-Agent"))
    _sync_domain_to_mail_api(domain)
    return jsonify({"ok": True}), 200


@admin_bp.route("/domains/<int:domain_id>/dav-config", methods=["POST"])
@require_role("admin")
def save_dav_config(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    domain.caldav_host = request.form.get("caldav_host", "").strip() or None
    domain.caldav_port = _parse_int(request.form.get("caldav_port"), domain.caldav_port)
    domain.caldav_use_tls = request.form.get("caldav_use_tls") == "1"
    domain.carddav_host = request.form.get("carddav_host", "").strip() or None
    domain.carddav_port = _parse_int(request.form.get("carddav_port"), domain.carddav_port)
    domain.carddav_use_tls = request.form.get("carddav_use_tls") == "1"
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "domain_dav_config", domain.name, request.remote_addr, request.headers.get("User-Agent"))
    return jsonify({"ok": True}), 200


@admin_bp.route("/domains/<int:domain_id>/accounts", methods=["GET"])
@require_role("admin")
def domain_accounts(domain_id):
    db.get_or_404(Domain, domain_id)
    accounts = (
        CustomerAccount.query
        .filter_by(domain_id=domain_id)
        .all()
    )
    rows = []
    for a in accounts:
        user = db.session.get(User, a.customer_id)
        rows.append({
            "id": a.id,
            "email": a.email_address,
            "auth_type": a.auth_type,
            "is_active": a.is_active,
            "user_active": user.is_active if user else False,
        })
    return jsonify({"accounts": rows}), 200


@admin_bp.route("/domains/<int:domain_id>/mail-api-config", methods=["POST"])
@require_role("admin")
def save_mail_api_config(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    domain.mail_api_url = request.form.get("mail_api_url", "").strip() or None
    domain.mail_api_key = request.form.get("mail_api_key", "").strip() or None
    db.session.commit()
    log_audit(session.get("user_id"), "admin", "domain_mail_api_config", domain.name, request.remote_addr, request.headers.get("User-Agent"))
    _sync_domain_to_mail_api(domain)
    return jsonify({"ok": True}), 200


@admin_bp.route("/domains/<int:domain_id>/test-mail-api", methods=["POST"])
@require_role("admin")
def test_mail_api_connection(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    url = request.form.get("mail_api_url", "").strip() or domain.mail_api_url or ""
    key = request.form.get("mail_api_key", "").strip() or domain.mail_api_key or ""
    if not url:
        return jsonify({"ok": False, "error": "No mail API URL configured."}), 200
    from app.admin.services.mail_server.http_client import MailApiClient
    client = MailApiClient(url, key)
    try:
        available = client.is_available()
        if available:
            return jsonify({"ok": True, "message": "Connection successful."}), 200
        return jsonify({"ok": False, "error": "Health check returned non-200."}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200


@admin_bp.route("/domains/<int:domain_id>/sync-preview", methods=["POST"])
@require_role("admin")
def sync_preview(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    from app.admin.services.mail_server import get_mail_client_for_domain
    client = get_mail_client_for_domain(domain)
    if client is None:
        return jsonify({"error": "No mail API configured for this domain."}), 400
    try:
        remote_users = client.list_users(domain.name)
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch remote accounts: {exc}"}), 200

    remote_emails = {u["email"] for u in remote_users}

    local_accounts = CustomerAccount.query.filter_by(domain_id=domain_id).all()
    local_emails = {a.email_address for a in local_accounts}

    remote_only = sorted(remote_emails - local_emails)
    local_only = sorted(local_emails - remote_emails)
    in_sync = sorted(remote_emails & local_emails)

    return jsonify({
        "remote_only": remote_only,
        "local_only": local_only,
        "in_sync": in_sync,
    })


@admin_bp.route("/domains/<int:domain_id>/sync-apply", methods=["POST"])
@require_role("admin")
def sync_apply(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    actions = request.get_json(silent=True) or {}
    create_locally = actions.get("create_locally", [])
    create_remotely = actions.get("create_remotely", [])
    soft_delete_locally = actions.get("soft_delete_locally", [])

    from app.admin.services.mail_server import get_mail_client_for_domain

    created = []
    for email in create_locally:
        user = User.query.filter_by(email=email).first()
        if user:
            existing_acc = CustomerAccount.query.filter_by(customer_id=user.id, domain_id=domain_id).first()
            if existing_acc:
                continue
        if not user:
            user = User(role="customer", email=email, is_active=False)
            db.session.add(user)
            db.session.flush()
        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain_id,
            email_address=email,
            auth_type="password",
            username=email,
            is_active=True,
        )
        db.session.add(account)
        created.append(email)

    client = get_mail_client_for_domain(domain)
    created_remote = []
    if client:
        for email in create_remotely:
            try:
                client.add_user(email, secrets.token_urlsafe(16))
                acc = CustomerAccount.query.filter_by(email_address=email, domain_id=domain_id).first()
                if acc:
                    acc.is_active = True
                created_remote.append(email)
            except Exception as exc:
                logger.warning("sync: failed to create remote user %s: %s", email, exc)

    deactivated = []
    for email in soft_delete_locally:
        acc = CustomerAccount.query.filter_by(email_address=email, domain_id=domain_id).first()
        if acc:
            acc.is_active = False
            deactivated.append(email)

    db.session.commit()

    log_audit(
        session.get("user_id"),
        "admin",
        "domain_sync_apply",
        f"domain={domain.name},created_local={len(created)},created_remote={len(created_remote)},deactivated={len(deactivated)}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )

    return jsonify({
        "created_locally": created,
        "created_remotely": created_remote,
        "soft_deleted_locally": deactivated,
    })


@admin_bp.route("/domains/<int:domain_id>/sync", methods=["GET"])
@require_role("admin")
def domain_sync(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    if not config or not config.is_self_hosted:
        return redirect(url_for("admin.review_domain_accounts", domain_id=domain_id))
    return render_template(
        "admin/domain_sync.html",
        domain=domain,
        active_page="domains",
        active_tab="accounts",
        title=f"Sync {domain.name}",
    )


@admin_bp.route("/domains/<int:domain_id>/accounts/<int:account_id>/reset-password", methods=["POST"])
@require_role("admin")
def account_reset_password(domain_id, account_id):
    domain = db.get_or_404(Domain, domain_id)
    account = db.get_or_404(CustomerAccount, account_id)
    if account.domain_id != domain_id:
        return jsonify({"ok": False, "error": "Account does not belong to this domain."}), 400

    body = request.get_json(silent=True) or {}
    password = body.get("password", "")
    if not password:
        return jsonify({"ok": False, "error": "Password is required."})

    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    if not config or not config.is_self_hosted:
        return jsonify({"ok": False, "error": "Domain is not self-hosted."})

    _mail_api_call(
        "update_mailbox",
        lambda c: _upsert_user(c, account.email_address, password),
        domain=domain,
    )

    account.auth_type = "password"
    account.signup_token = None
    account.signup_expires_at = None
    if account.cache_db_path:
        purge_cache(account.cache_db_path)
        account.cache_db_path = None
    account.dek_wrapped_cred = None
    db.session.commit()

    log_audit(
        session.get("user_id"), "admin", "account_password_reset",
        f"email={account.email_address}", request.remote_addr, request.headers.get("User-Agent"),
    )
    return jsonify({"ok": True, "email": account.email_address})


@admin_bp.route("/domains/<int:domain_id>/accounts/<int:account_id>/login-link", methods=["POST"])
@require_role("admin")
def account_login_link(domain_id, account_id):
    db.get_or_404(Domain, domain_id)
    account = db.get_or_404(CustomerAccount, account_id)
    if account.domain_id != domain_id:
        return jsonify({"ok": False, "error": "Account does not belong to this domain."}), 400

    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    if not config or not config.is_self_hosted:
        return jsonify({"ok": False, "error": "Domain is not self-hosted."})

    token = secrets.token_urlsafe(32)
    account.signup_token = token
    account.signup_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.session.commit()

    login_url = f"{current_app.config['APP_URL'].rstrip('/')}/signup/{token}"
    log_audit(
        session.get("user_id"), "admin", "account_login_link",
        f"email={account.email_address}", request.remote_addr, request.headers.get("User-Agent"),
    )
    return jsonify({"ok": True, "login_url": login_url, "email": account.email_address})


@admin_bp.route("/domains/<int:domain_id>/accounts/<int:account_id>/delete", methods=["POST"])
@require_role("admin")
def account_delete(domain_id, account_id):
    domain = db.get_or_404(Domain, domain_id)
    account = db.get_or_404(CustomerAccount, account_id)
    if account.domain_id != domain_id:
        return jsonify({"ok": False, "error": "Account does not belong to this domain."}), 400

    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    if config and config.is_self_hosted:
        _mail_api_call(
            "delete_mailbox",
            lambda c: c.remove_user(account.email_address),
            domain=domain,
        )

    if account.cache_db_path:
        purge_cache(account.cache_db_path)
    user = db.session.get(User, account.customer_id)
    db.session.delete(account)
    if user and user.role == "customer":
        db.session.delete(user)
    db.session.commit()

    log_audit(
        session.get("user_id"), "admin", "account_delete",
        f"email={account.email_address}", request.remote_addr, request.headers.get("User-Agent"),
    )
    return jsonify({"ok": True, "email": account.email_address})


@admin_bp.app_template_filter("import_token")
def import_token_filter(import_request):
    return build_import_token(import_request)


@admin_bp.route("/platform-dns")
@require_role("admin")
def platform_dns():
    entries = PlatformDnsConfig.query.order_by(PlatformDnsConfig.mx_priority, PlatformDnsConfig.id).all()
    svc = PlatformServiceConfig.query.first()
    return render_template(
        "admin/platform_dns.html",
        entries=entries,
        svc=svc,
        active_page="platform_dns",
        title="Platform Configuration",
    )


@admin_bp.route("/platform-dns/save", methods=["POST"])
@require_role("admin")
def save_platform_dns():
    hostnames_raw = request.form.get("mx_hostnames", "").strip()
    priorities_raw = request.form.get("mx_priorities", "").strip()

    hostnames = [h.strip() for h in hostnames_raw.splitlines() if h.strip()]
    priorities_str = [p.strip() for p in priorities_raw.splitlines() if p.strip()]

    entries = []
    for i, host in enumerate(hostnames):
        try:
            prio = int(priorities_str[i]) if i < len(priorities_str) else 10 + i * 10
        except (ValueError, IndexError):
            prio = 10 + i * 10
        entries.append({"host": host, "priority": prio})

    PlatformDnsConfig.query.delete()
    for entry in entries:
        row = PlatformDnsConfig(
            mx_hostname=entry["host"],
            mx_priority=entry["priority"],
        )
        db.session.add(row)

    svc = PlatformServiceConfig.query.first()
    if svc is None:
        svc = PlatformServiceConfig()
        db.session.add(svc)
    svc.imap_host = request.form.get("imap_host", "").strip() or None
    svc.imap_port = _parse_int(request.form.get("imap_port"), 993)
    svc.imap_tls = request.form.get("imap_tls") == "1"
    svc.smtp_host = request.form.get("smtp_host", "").strip() or None
    svc.smtp_port = _parse_int(request.form.get("smtp_port"), 587)
    svc.smtp_tls_mode = request.form.get("smtp_tls_mode", "starttls").strip() or "starttls"
    svc.carddav_host = request.form.get("carddav_host", "").strip() or None
    svc.carddav_port = _parse_int(request.form.get("carddav_port"), 5232)
    svc.carddav_use_tls = request.form.get("carddav_use_tls") == "1"
    svc.caldav_host = request.form.get("caldav_host", "").strip() or None
    svc.caldav_port = _parse_int(request.form.get("caldav_port"), 5232)
    svc.caldav_use_tls = request.form.get("caldav_use_tls") == "1"

    db.session.commit()
    log_audit(
        session.get("user_id"),
        "admin",
        "platform_dns_save",
        f"entries={len(entries)}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    flash(f"Platform configuration saved ({len(entries)} MX server{'s' if len(entries) != 1 else ''}).", "success")
    return redirect(url_for("admin.platform_dns"))


@admin_bp.route("/platform-dns/validate", methods=["POST"])
@require_role("admin")
def validate_platform_dns():
    hostname = (request.get_json(silent=True) or {}).get("hostname", "").strip()
    if not hostname:
        return jsonify({"ok": False, "error": "Hostname is required."}), 200
    from app.admin.services.dns_checks import validate_mx_hostname
    try:
        result = validate_mx_hostname(hostname)
        return jsonify({"ok": True, "result": result}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200


@admin_bp.route("/domains/<int:domain_id>/self-hosted", methods=["POST"])
@require_role("admin")
def save_self_hosted(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    is_self_hosted = request.form.get("is_self_hosted") == "1"
    dkim_selector = request.form.get("dkim_selector", "default").strip() or "default"
    dmarc_policy = request.form.get("dmarc_policy", "none")
    dmarc_rua = request.form.get("dmarc_rua", "").strip() or None

    if is_self_hosted and not domain.mail_api_url:
        return jsonify({"ok": False, "error": "Mail API URL is required for self-hosted domains. Configure it in the Mail API section above."}), 200

    if dmarc_policy not in ("none", "quarantine", "reject"):
        dmarc_policy = "none"

    import re
    if not re.match(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$', dkim_selector):
        return jsonify({"ok": False, "error": "DKIM selector must be 1-63 characters, lowercase alphanumeric and hyphens only."}), 200

    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    if config is None:
        config = DomainDnsConfig(domain_id=domain_id)
        db.session.add(config)

    config.is_self_hosted = is_self_hosted
    config.dkim_selector = dkim_selector
    config.dmarc_policy = dmarc_policy
    config.dmarc_rua = dmarc_rua

    db.session.commit()
    log_audit(
        session.get("user_id"),
        "admin",
        "domain_self_hosted",
        f"domain={domain.name},self_hosted={is_self_hosted}",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    return jsonify({"ok": True}), 200


@admin_bp.route("/domains/<int:domain_id>/dkim-key", methods=["GET"])
@require_role("admin")
def get_dkim_key(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    from app.admin.services.mail_server import get_mail_client_for_domain
    client = get_mail_client_for_domain(domain)
    if client is None:
        return jsonify({"has_key": False}), 200
    try:
        selector = config.dkim_selector if config else None
        dkim_data = client.get_dkim_key(domain.name, selector=selector)
        return jsonify({"has_key": True, "public_key": dkim_data.get("public_key"), "selector": dkim_data.get("selector"), "txt_record": dkim_data.get("txt_record")}), 200
    except Exception:
        return jsonify({"has_key": False}), 200


@admin_bp.route("/domains/<int:domain_id>/dkim-generate", methods=["POST"])
@require_role("admin")
def generate_dkim_key(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()
    if not config or not config.is_self_hosted:
        return jsonify({"ok": False, "error": "Self-hosted must be enabled first."}), 200
    from app.admin.services.mail_server import get_mail_client_for_domain
    client = get_mail_client_for_domain(domain)
    if client is None:
        return jsonify({"ok": False, "error": "Mail API is not configured."}), 200
    try:
        dkim_data = client.generate_dkim_key(domain.name, selector=config.dkim_selector)
        log_audit(session.get("user_id"), "admin", "dkim_generate", domain.name, request.remote_addr, request.headers.get("User-Agent"))
        return jsonify({"ok": True, "public_key": dkim_data.get("public_key"), "selector": dkim_data.get("selector"), "txt_record": dkim_data.get("txt_record")}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200


@admin_bp.route("/domains/<int:domain_id>/dns-check", methods=["POST"])
@require_role("admin")
def dns_check(domain_id):
    domain = db.get_or_404(Domain, domain_id)
    config = DomainDnsConfig.query.filter_by(domain_id=domain_id).first()

    if not config or not config.is_self_hosted:
        return jsonify({"error": "Domain is not self-hosted."}), 400

    platform_entries = PlatformDnsConfig.query.order_by(PlatformDnsConfig.mx_priority).all()
    if not platform_entries:
        return jsonify({"error": "No platform MX servers configured. Set up Platform DNS first."}), 200

    mx_servers = [{"host": e.mx_hostname, "priority": e.mx_priority} for e in platform_entries]
    dkim_selector = config.dkim_selector

    dkim_public_key = None
    from app.admin.services.mail_server import get_mail_client_for_domain

    client = get_mail_client_for_domain(domain)
    if client:
        try:
            dkim_data = client.get_dkim_key(domain.name, selector=dkim_selector)
            dkim_public_key = dkim_data.get("public_key")
        except Exception:
            pass

    from app.admin.services.dns_checks import run_all_dns_checks
    try:
        results = run_all_dns_checks(
            domain_name=domain.name,
            mx_servers=mx_servers,
            dkim_selector=dkim_selector,
            dkim_public_key=dkim_public_key,
            dmarc_policy=config.dmarc_policy,
            dmarc_rua=config.dmarc_rua,
        )
        return jsonify(results), 200
    except Exception as exc:
        logger.error("DNS check failed for %s: %s", domain.name, exc, exc_info=True)
        return jsonify({"error": f"DNS check failed: {exc}"}), 200


def _apply_discovery(domain: Domain, discovery):
    imap_primary = discovery.get("imap_primary")
    smtp_primary = discovery.get("smtp_primary")

    if imap_primary:
        domain.imap_host = imap_primary.host
        domain.imap_port = imap_primary.port
    if smtp_primary:
        domain.smtp_host = smtp_primary.host
        domain.smtp_port = smtp_primary.port
        domain.smtp_tls_mode = smtp_primary.tls_mode or domain.smtp_tls_mode

    if _missing_domain_fields(domain):
        domain.status = "review"
        return
    if len(discovery.get("imap_candidates", [])) > 1 or len(discovery.get("smtp_candidates", [])) > 1:
        domain.status = "review"
        return
    domain.status = "complete"


def _compute_domain_status(domain: Domain) -> str:
    return "complete" if not _missing_domain_fields(domain) else "review"


def _missing_domain_fields(domain: Domain):
    missing = []
    if not domain.imap_host:
        missing.append("IMAP host")
    if not domain.imap_port:
        missing.append("IMAP port")
    if not domain.smtp_host:
        missing.append("SMTP host")
    if not domain.smtp_port:
        missing.append("SMTP port")
    if not domain.smtp_tls_mode:
        missing.append("SMTP TLS mode")
    return missing


def _needs_review(discovery, domain: Domain) -> bool:
    if _missing_domain_fields(domain):
        return True
    if len(discovery.get("imap_candidates", [])) > 1:
        return True
    if len(discovery.get("smtp_candidates", [])) > 1:
        return True
    return False


def _apply_domain_form(domain: Domain, form):
    imap_candidate = form.get("imap_candidate")
    smtp_candidate = form.get("smtp_candidate")

    imap_host = form.get("imap_host", "").strip()
    smtp_host = form.get("smtp_host", "").strip()

    if not imap_host and imap_candidate:
        host, port = imap_candidate.split("|", 1)
        domain.imap_host = host
        domain.imap_port = _parse_int(port, domain.imap_port)
    else:
        domain.imap_host = imap_host or domain.imap_host
        domain.imap_port = _parse_int(form.get("imap_port"), domain.imap_port)

    if not smtp_host and smtp_candidate:
        host, port, tls_mode = smtp_candidate.split("|", 2)
        domain.smtp_host = host
        domain.smtp_port = _parse_int(port, domain.smtp_port)
        domain.smtp_tls_mode = tls_mode
    else:
        domain.smtp_host = smtp_host or domain.smtp_host
        domain.smtp_port = _parse_int(form.get("smtp_port"), domain.smtp_port)
        domain.smtp_tls_mode = form.get("smtp_tls_mode", domain.smtp_tls_mode)

    domain.imap_auth_methods = form.get("imap_auth_methods", domain.imap_auth_methods)
    domain.smtp_auth_methods = form.get("smtp_auth_methods", domain.smtp_auth_methods)
    domain.caldav_host = form.get("caldav_host", "").strip() or None
    domain.caldav_port = _parse_int(form.get("caldav_port"), domain.caldav_port)
    domain.caldav_use_tls = form.get("caldav_use_tls") == "1"
    domain.carddav_host = form.get("carddav_host", "").strip() or None
    domain.carddav_port = _parse_int(form.get("carddav_port"), domain.carddav_port)
    domain.carddav_use_tls = form.get("carddav_use_tls") == "1"
    domain.mail_api_url = form.get("mail_api_url", "").strip() or None
    domain.mail_api_key = form.get("mail_api_key", "").strip() or None


def _parse_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _sync_domains(domains=None):
    if domains is None:
        domains = Domain.query.all()
    global_mail_api = current_app.config.get("MAIL_API_URL", "")
    return [d for d in domains if d.mail_api_url or global_mail_api]


def _sync_link(domain):
    return f'<a href="{url_for("admin.domain_sync", domain_id=domain.id)}" class="underline font-medium">sync email accounts</a>'


def _flash_user_exists(email, domain):
    from app.admin.services.mail_server import get_mail_client_for_domain
    client = get_mail_client_for_domain(domain)
    if client:
        flash(Markup(f"User {email} already exists. Use {_sync_link(domain)} to import them."), "error")
    else:
        flash(f"User {email} already exists.", "error")


def _flash_mailbox_exists(email, domain, exc=None):
    if exc:
        flash(Markup(f"Failed to create mailbox: {exc}. Use {_sync_link(domain)} to import it."), "error")
    else:
        flash(Markup(f"Mailbox {email} already exists on the mail server. Use {_sync_link(domain)} to import it."), "error")


def _upsert_user(client, email, password):
    try:
        client.add_user(email, password)
    except Exception:
        client.set_password(email, password)


def _mail_api_call(action_label, func, domain=None):
    from app.admin.services.mail_server import get_mail_client_for_domain
    client = get_mail_client_for_domain(domain)
    if client is None:
        return
    try:
        func(client)
    except Exception as exc:
        logger.error("mail-api %s failed: %s", action_label, exc)
        flash(f"Mail server error ({action_label}): {exc}", "error")


def _sync_domain_to_mail_api(domain: Domain):
    if domain.is_active and domain.status == "complete":
        _mail_api_call("add_domain", lambda c: c.add_domain(domain.name), domain=domain)
    else:
        _mail_api_call("remove_domain", lambda c: c.remove_domain(domain.name), domain=domain)
