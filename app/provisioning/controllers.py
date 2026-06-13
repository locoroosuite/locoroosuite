from __future__ import annotations

import functools
import logging

from flask import Blueprint, current_app, request, jsonify

logger = logging.getLogger(__name__)

provision_bp = Blueprint("provision", __name__, url_prefix="/api/provision")


def _check_provisioning_auth():
    key = current_app.config.get("PROVISIONING_API_KEY", "")
    if not key:
        return False
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == key
    return False


def require_provisioning_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _check_provisioning_auth():
            return jsonify({"error": {"code": "UNAUTHORIZED", "message": "Invalid or missing provisioning API key"}}), 401
        return f(*args, **kwargs)
    return decorated


def _ok(data=None, status=200):
    body = {}
    if data is not None:
        body = data
    return jsonify(body), status


def _error(code, message, status=400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _get_mail_client():
    from app.admin.services.mail_server import get_mail_client
    client = get_mail_client()
    if not client:
        raise RuntimeError("Mail API is not configured")
    return client


@provision_bp.route("/check-availability", methods=["POST"])
@require_provisioning_auth
def check_availability():
    body = request.get_json(silent=True) or {}
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        return _error("VALIDATION_ERROR", "email is required and must be valid")
    try:
        from app.shared.models.core import User

        local_user = User.query.filter_by(email=email).first()
        if local_user:
            return _ok({"available": False})
        client = _get_mail_client()
        exists = client.check_user(email)
        return _ok({"available": not exists})
    except Exception as exc:
        logger.error("check-availability failed for %s: %s", email, exc)
        return _error("SERVICE_ERROR", str(exc), 503)


@provision_bp.route("/create-domain", methods=["POST"])
@require_provisioning_auth
def create_domain():
    body = request.get_json(silent=True) or {}
    domain = body.get("domain", "").strip().lower()
    if not domain:
        return _error("VALIDATION_ERROR", "domain is required")
    try:
        from app.shared.models.core import Domain, DomainDnsConfig, PlatformServiceConfig
        from app.shared.db import db

        domain_obj = Domain.query.filter_by(name=domain).first()
        if not domain_obj:
            is_dev = current_app.config.get("APP_ENV") == "development"
            global_mail_url = current_app.config.get("MAIL_API_URL", "")
            global_mail_key = current_app.config.get("MAIL_API_KEY", "")

            svc = None
            if not is_dev:
                svc = PlatformServiceConfig.query.first()

            if is_dev:
                domain_obj = Domain(
                    name=domain,
                    status="complete",
                    imap_host="dovecot",
                    imap_port=143,
                    imap_tls=False,
                    smtp_host="postfix",
                    smtp_port=587,
                    smtp_tls_mode="starttls",
                    carddav_host="radicale",
                    carddav_port=5232,
                    carddav_use_tls=False,
                    caldav_host="radicale",
                    caldav_port=5232,
                    caldav_use_tls=False,
                )
            elif svc:
                domain_obj = Domain(
                    name=domain,
                    status="complete",
                    imap_host=svc.imap_host or "",
                    imap_port=svc.imap_port,
                    imap_tls=svc.imap_tls,
                    smtp_host=svc.smtp_host or "",
                    smtp_port=svc.smtp_port,
                    smtp_tls_mode=svc.smtp_tls_mode,
                    carddav_host=svc.carddav_host,
                    carddav_port=svc.carddav_port,
                    carddav_use_tls=svc.carddav_use_tls,
                    caldav_host=svc.caldav_host,
                    caldav_port=svc.caldav_port,
                    caldav_use_tls=svc.caldav_use_tls,
                    mail_api_url=global_mail_url or None,
                    mail_api_key=global_mail_key or None,
                )
            else:
                domain_obj = Domain(
                    name=domain,
                    status="review",
                    imap_host="",
                    imap_port=993,
                    imap_tls=True,
                    smtp_host="",
                    smtp_port=587,
                    smtp_tls_mode="starttls",
                    mail_api_url=global_mail_url or None,
                    mail_api_key=global_mail_key or None,
                )

            db.session.add(domain_obj)
            db.session.flush()

            if not is_dev and (global_mail_url or svc):
                dns_config = DomainDnsConfig(
                    domain_id=domain_obj.id,
                    is_self_hosted=True,
                )
                db.session.add(dns_config)

            db.session.commit()

        client = _get_mail_client()
        client.add_domain(domain)
        return _ok({"created": True, "domain": domain}, 201)
    except Exception as exc:
        logger.error("create-domain failed for %s: %s", domain, exc)
        return _error("DOMAIN_CREATE_FAILED", str(exc), 500)


@provision_bp.route("/create-mailbox", methods=["POST"])
@require_provisioning_auth
def create_mailbox():
    body = request.get_json(silent=True) or {}
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    domain = body.get("domain", "").strip().lower()
    quota_bytes = body.get("quota_bytes")
    max_emails_per_day = body.get("max_emails_per_day")

    if not email or not password:
        return _error("VALIDATION_ERROR", "email and password are required")
    if "@" not in email:
        return _error("VALIDATION_ERROR", "email must contain @")

    try:
        client = _get_mail_client()

        if domain:
            client.add_domain(domain)

        client.add_user(email, password, quota_bytes=quota_bytes)

        if max_emails_per_day is not None:
            client.set_sending_limit(email, max_emails_per_day)

        from app.shared.models.core import User, Domain, CustomerAccount
        from app.shared.db import db
        from werkzeug.security import generate_password_hash

        local, domain_name = email.rsplit("@", 1)
        domain_obj = Domain.query.filter_by(name=domain_name).first()

        existing_user = User.query.filter_by(email=email).first()
        if not existing_user:
            new_user = User(
                role="customer",
                email=email,
                password_hash=generate_password_hash(password),
                is_active=True,
            )
            db.session.add(new_user)
            db.session.flush()

            if domain_obj:
                account = CustomerAccount(
                    customer_id=new_user.id,
                    domain_id=domain_obj.id,
                    email_address=email,
                    auth_type="password",
                    username=email,
                    is_active=True,
                )
                db.session.add(account)

            db.session.commit()

        return _ok({"created": True, "email": email}, 201)
    except Exception as exc:
        logger.error("create-mailbox failed for %s: %s", email, exc)
        if "already exists" in str(exc).lower():
            return _error("MAILBOX_EXISTS", str(exc), 409)
        return _error("MAILBOX_CREATE_FAILED", str(exc), 500)


@provision_bp.route("/mailbox/<path:email>", methods=["DELETE"])
@require_provisioning_auth
def delete_mailbox(email):
    email = email.strip().lower()
    try:
        client = _get_mail_client()
        client.remove_user(email)
        try:
            client.delete_sending_limit(email)
        except Exception:
            pass
        return _ok({"deleted": True})
    except FileNotFoundError:
        return _error("MAILBOX_NOT_FOUND", f"Mailbox {email} not found", 404)
    except Exception as exc:
        logger.error("delete-mailbox failed for %s: %s", email, exc)
        return _error("MAILBOX_DELETE_FAILED", str(exc), 500)


@provision_bp.route("/users/<path:domain>", methods=["GET"])
@require_provisioning_auth
def list_users(domain):
    domain = domain.strip().lower()
    try:
        client = _get_mail_client()
        users = client.list_users(domain=domain)
        return _ok({"data": users})
    except Exception as exc:
        logger.error("list-users failed for %s: %s", domain, exc)
        return _error("SERVICE_ERROR", str(exc), 503)


@provision_bp.route("/generate-dkim", methods=["POST"])
@require_provisioning_auth
def generate_dkim():
    body = request.get_json(silent=True) or {}
    domain = body.get("domain", "").strip().lower()
    selector = body.get("selector") or None
    if not domain:
        return _error("VALIDATION_ERROR", "domain is required")
    try:
        client = _get_mail_client()
        key_data = client.generate_dkim_key(domain, selector=selector)
        return _ok({
            "selector": key_data.get("selector", "default"),
            "public_key": key_data.get("public_key", ""),
            "txt_record": key_data.get("txt_record", ""),
        }, 201)
    except Exception as exc:
        logger.error("generate-dkim failed for %s: %s", domain, exc)
        return _error("DKIM_GENERATE_FAILED", str(exc), 500)


@provision_bp.route("/dns-records/<path:domain>", methods=["GET"])
@require_provisioning_auth
def get_dns_records(domain):
    domain = domain.strip().lower()
    try:
        from app.shared.models.core import PlatformDnsConfig, Domain, DomainDnsConfig

        platform_dns = PlatformDnsConfig.query.first()
        domain_obj = Domain.query.filter_by(name=domain).first()
        domain_dns = DomainDnsConfig.query.filter_by(domain_id=domain_obj.id).first() if domain_obj else None

        mx_servers = []
        if platform_dns and platform_dns.mx_hostname:
            mx_servers = [{"host": platform_dns.mx_hostname, "priority": platform_dns.mx_priority or 10}]

        spf_record = "v=spf1 mx ~all"
        mx_hosts = [s.get("host", "") for s in mx_servers if s.get("host")]

        dkim_data = None
        try:
            client = _get_mail_client()
            dkim_selector = domain_dns.dkim_selector if domain_dns else None
            key_data = client.get_dkim_key(domain, selector=dkim_selector)
            dkim_data = {
                "selector": key_data.get("selector", "default"),
                "txt_record": key_data.get("txt_record", ""),
            }
        except Exception:
            pass

        dmarc_policy = "none"
        dmarc_rua = None
        if domain_dns:
            dmarc_policy = domain_dns.dmarc_policy or "none"
            dmarc_rua = domain_dns.dmarc_rua

        dmarc_parts = [f"v=DMARC1; p={dmarc_policy}"]
        if dmarc_rua:
            dmarc_parts.append(f"rua=mailto:{dmarc_rua}")
        dmarc_record = "; ".join(dmarc_parts)

        mx_lines = []
        for s in mx_servers:
            priority = s.get("priority", 10)
            host = s.get("host", "")
            if host:
                mx_lines.append(f"@  IN  MX  {priority}  {host}.")

        return _ok({
            "mx": "\n".join(mx_lines),
            "mx_hosts": mx_hosts,
            "spf": spf_record,
            "dkim": dkim_data,
            "dmarc": dmarc_record,
        })
    except Exception as exc:
        logger.error("dns-records failed for %s: %s", domain, exc)
        return _error("DNS_RECORDS_FAILED", str(exc), 500)


@provision_bp.route("/validate-dns/<path:domain>", methods=["POST"])
@require_provisioning_auth
def validate_dns(domain):
    domain = domain.strip().lower()
    try:
        from app.shared.models.core import PlatformDnsConfig
        from app.admin.services.dns_checks import run_all_dns_checks

        platform_dns = PlatformDnsConfig.query.first()
        mx_servers = []
        if platform_dns and platform_dns.mx_hostname:
            mx_servers = [{"host": platform_dns.mx_hostname, "priority": platform_dns.mx_priority or 10}]

        dkim_selector = "default"
        dkim_public_key = None
        try:
            client = _get_mail_client()
            key_data = client.get_dkim_key(domain)
            dkim_public_key = key_data.get("public_key")
        except Exception:
            pass

        dmarc_policy = "none"
        dmarc_rua = None

        results = run_all_dns_checks(
            domain_name=domain,
            mx_servers=mx_servers,
            dkim_selector=dkim_selector,
            dkim_public_key=dkim_public_key,
            dmarc_policy=dmarc_policy,
            dmarc_rua=dmarc_rua,
        )
        return _ok(results)
    except Exception as exc:
        logger.error("validate-dns failed for %s: %s", domain, exc)
        return _error("DNS_VALIDATION_FAILED", str(exc), 500)


@provision_bp.route("/validate-ownership/<path:domain>", methods=["POST"])
@require_provisioning_auth
def validate_ownership(domain):
    domain = domain.strip().lower()
    body = request.get_json(silent=True) or {}
    expected_value = body.get("expected_value", "").strip()
    if not expected_value:
        return _error("VALIDATION_ERROR", "expected_value is required")

    try:
        from app.admin.services.dns_checks import _get_authoritative_nameservers, _resolve_ns_ips, _query_record_at_ns

        ns_names = _get_authoritative_nameservers(domain)
        if not ns_names:
            return _ok({"verified": False, "found": [], "details": "Could not resolve authoritative nameservers"})

        ns_ips = _resolve_ns_ips(ns_names)
        if not ns_ips:
            return _ok({"verified": False, "found": [], "details": "Could not resolve nameserver IPs"})

        all_found: list[str] = []
        verified_count = 0
        for ip in ns_ips:
            records = _query_record_at_ns(domain, "TXT", ip)
            for rec in records:
                all_found.append(rec)
                if rec.strip() == expected_value.strip():
                    verified_count += 1
                    break

        verified = verified_count == len(ns_ips)
        details = f"{verified_count}/{len(ns_ips)} nameservers have matching TXT record"

        return _ok({"verified": verified, "found": all_found, "details": details})
    except Exception as exc:
        logger.error("validate-ownership failed for %s: %s", domain, exc)
        return _error("OWNERSHIP_VALIDATION_FAILED", str(exc), 500)


@provision_bp.route("/update-dmarc-rua", methods=["POST"])
@require_provisioning_auth
def update_dmarc_rua():
    body = request.get_json(silent=True) or {}
    domain_name = body.get("domain", "").strip().lower()
    dmarc_rua = body.get("dmarc_rua", "").strip() or None
    if not domain_name:
        return _error("VALIDATION_ERROR", "domain is required")
    try:
        from app.shared.models.core import Domain, DomainDnsConfig
        from app.shared.db import db

        domain_obj = Domain.query.filter_by(name=domain_name).first()
        if not domain_obj:
            return _error("NOT_FOUND", f"Domain {domain_name} not found")

        config = DomainDnsConfig.query.filter_by(domain_id=domain_obj.id).first()
        if config is None:
            config = DomainDnsConfig(domain_id=domain_obj.id)
            db.session.add(config)

        config.dmarc_rua = dmarc_rua
        db.session.commit()

        return _ok({"domain": domain_name, "dmarc_rua": dmarc_rua})
    except Exception as exc:
        logger.error("update-dmarc-rua failed for %s: %s", domain_name, exc)
        return _error("DMARC_UPDATE_FAILED", str(exc), 500)


@provision_bp.route("/mailbox/<path:email>/quota", methods=["PUT"])
@require_provisioning_auth
def update_quota(email):
    email = email.strip().lower()
    body = request.get_json(silent=True) or {}
    quota_bytes = body.get("quota_bytes")
    if quota_bytes is None or not isinstance(quota_bytes, int) or quota_bytes < 0:
        return _error("VALIDATION_ERROR", "quota_bytes must be a non-negative integer")
    try:
        client = _get_mail_client()
        client.set_quota(email, quota_bytes)
        return _ok({"updated": True})
    except Exception as exc:
        logger.error("update-quota failed for %s: %s", email, exc)
        return _error("QUOTA_UPDATE_FAILED", str(exc), 500)


@provision_bp.route("/mailbox/<path:email>/password", methods=["PUT"])
@require_provisioning_auth
def update_password(email):
    email = email.strip().lower()
    body = request.get_json(silent=True) or {}
    password = body.get("password", "")
    if not password or len(password) < 8:
        return _error("VALIDATION_ERROR", "password must be at least 8 characters")
    try:
        client = _get_mail_client()
        client.set_password(email, password)
        return _ok({"updated": True})
    except Exception as exc:
        logger.error("update-password failed for %s: %s", email, exc)
        return _error("PASSWORD_UPDATE_FAILED", str(exc), 500)
