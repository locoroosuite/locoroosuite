from datetime import datetime, timezone

from app.shared.db import db


def _utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(16), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    totp_secret = db.Column(db.String(64), nullable=True)
    totp_enabled = db.Column(db.Boolean, default=False, nullable=False)
    backup_codes = db.Column(db.Text, nullable=True)


class Domain(db.Model):
    __tablename__ = "domains"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    status = db.Column(db.String(16), default="draft", nullable=False)

    imap_host = db.Column(db.String(255), nullable=False)
    imap_port = db.Column(db.Integer, nullable=False, default=993)
    imap_tls = db.Column(db.Boolean, default=True, nullable=False)
    imap_auth_methods = db.Column(db.String(255), nullable=True)

    smtp_host = db.Column(db.String(255), nullable=False)
    smtp_port = db.Column(db.Integer, nullable=False, default=587)
    smtp_tls_mode = db.Column(db.String(16), nullable=False, default="starttls")
    smtp_auth_methods = db.Column(db.String(255), nullable=True)

    carddav_host = db.Column(db.String(255), nullable=True)
    carddav_port = db.Column(db.Integer, default=5232, nullable=True)
    carddav_use_tls = db.Column(db.Boolean, default=False, nullable=True)

    caldav_host = db.Column(db.String(255), nullable=True)
    caldav_port = db.Column(db.Integer, default=5232, nullable=True)
    caldav_use_tls = db.Column(db.Boolean, default=False, nullable=True)

    mail_api_url = db.Column(db.String(512), nullable=True)
    mail_api_key = db.Column(db.String(255), nullable=True)


class ManagerDomain(db.Model):
    __tablename__ = "manager_domains"
    manager_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), primary_key=True)


class CustomerAccount(db.Model):
    __tablename__ = "customer_accounts"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), nullable=False)
    email_address = db.Column(db.String(255), nullable=False)
    auth_type = db.Column(db.String(16), nullable=False, default="password")
    username = db.Column(db.String(255), nullable=False)
    encrypted_secret = db.Column(db.LargeBinary, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    cache_db_path = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    api_enabled = db.Column(db.Boolean, default=False, nullable=False)
    dek_wrapped_cred = db.Column(db.LargeBinary, nullable=True)
    signup_token = db.Column(db.String(128), nullable=True, unique=True)
    signup_expires_at = db.Column(db.DateTime, nullable=True)


class CustomerSettings(db.Model):
    __tablename__ = "customer_settings"
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    polling_interval = db.Column(db.Integer, default=60, nullable=False)
    preview_pane_default = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.String(32), default="date_desc", nullable=False)
    timezone = db.Column(db.String(64), default="browser", nullable=False)
    date_format = db.Column(db.String(32), default="DD/MM/YYYY", nullable=False)
    theme = db.Column(db.String(16), default="light", nullable=False)
    pinned_folders = db.Column(db.Text, nullable=True)
    spam_action_prefs = db.Column(db.Text, nullable=True)
    protected_folders = db.Column(db.Text, nullable=True)
    protect_starred = db.Column(db.Boolean, default=True, nullable=False)
    locked_keyword_prefs = db.Column(db.Text, nullable=True)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, nullable=True)
    actor_role = db.Column(db.String(16), nullable=True)
    action = db.Column(db.String(64), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), nullable=False)
    ip_address = db.Column(db.String(64), nullable=False)
    failed_count = db.Column(db.Integer, default=0, nullable=False)
    first_failed_at = db.Column(db.DateTime, nullable=True)
    locked_until = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class ApiToken(db.Model):
    __tablename__ = "api_tokens"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    scopes = db.Column(db.Text, nullable=False)
    wrapped_dek = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    last_used_at = db.Column(db.DateTime, nullable=True)


class ApiRateLimitConfig(db.Model):
    __tablename__ = "api_rate_limit_config"
    id = db.Column(db.Integer, primary_key=True)
    default_requests_per_minute = db.Column(db.Integer, default=60, nullable=False)


class PlatformDnsConfig(db.Model):
    __tablename__ = "platform_dns_config"
    id = db.Column(db.Integer, primary_key=True)
    mx_hostname = db.Column(db.String(255), nullable=False)
    mx_priority = db.Column(db.Integer, nullable=False, default=10)


class PlatformServiceConfig(db.Model):
    __tablename__ = "platform_service_config"
    id = db.Column(db.Integer, primary_key=True)
    imap_host = db.Column(db.String(255), nullable=True)
    imap_port = db.Column(db.Integer, default=993, nullable=False)
    imap_tls = db.Column(db.Boolean, default=True, nullable=False)
    smtp_host = db.Column(db.String(255), nullable=True)
    smtp_port = db.Column(db.Integer, default=587, nullable=False)
    smtp_tls_mode = db.Column(db.String(16), default="starttls", nullable=False)
    carddav_host = db.Column(db.String(255), nullable=True)
    carddav_port = db.Column(db.Integer, default=5232, nullable=True)
    carddav_use_tls = db.Column(db.Boolean, default=False, nullable=True)
    caldav_host = db.Column(db.String(255), nullable=True)
    caldav_port = db.Column(db.Integer, default=5232, nullable=True)
    caldav_use_tls = db.Column(db.Boolean, default=False, nullable=True)


class DomainDnsConfig(db.Model):
    __tablename__ = "domain_dns_config"
    id = db.Column(db.Integer, primary_key=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), nullable=False, unique=True)
    is_self_hosted = db.Column(db.Boolean, default=False, nullable=False)
    dkim_selector = db.Column(db.String(64), default="default", nullable=False)
    dmarc_policy = db.Column(db.String(16), default="none", nullable=False)
    dmarc_rua = db.Column(db.String(255), nullable=True)


class DocShare(db.Model):
    __tablename__ = "doc_shares"
    id = db.Column(db.Integer, primary_key=True)
    doc_id = db.Column(db.String(64), nullable=False, index=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    owner_account_id = db.Column(db.Integer, nullable=False)
    share_token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    permission = db.Column(db.String(16), nullable=False, default="view")
    share_type = db.Column(db.String(16), nullable=False, default="link")
    recipient_email = db.Column(db.String(255), nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    view_count = db.Column(db.Integer, default=0, nullable=False)
    last_accessed_at = db.Column(db.DateTime, nullable=True)
    doc_name = db.Column(db.String(255), nullable=True)
    doc_type = db.Column(db.String(16), nullable=True, default="odt")
    doc_size = db.Column(db.Integer, default=0, nullable=False)
    doc_updated_at = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class TrustedDevice(db.Model):
    __tablename__ = "trusted_devices"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    user_agent = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    last_used_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
