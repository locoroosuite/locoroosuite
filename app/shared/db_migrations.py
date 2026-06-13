from sqlalchemy import inspect, text

from app.shared.db import db


def ensure_domain_status_column():
    inspector = inspect(db.engine)
    if "domains" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("domains")}
    if "status" in columns:
        return
    db.session.execute(
        text("ALTER TABLE domains ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'draft'")
    )
    from app.shared.models.core import Domain

    for domain in Domain.query.all():
        status = "complete"
        if not domain.imap_host or not domain.imap_port:
            status = "review"
        if not domain.smtp_host or not domain.smtp_port or not domain.smtp_tls_mode:
            status = "review"
        domain.status = status
    db.session.commit()


def ensure_customer_settings_spam_action_column():
    inspector = inspect(db.engine)
    if "customer_settings" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("customer_settings")}
    if "spam_action_prefs" in columns:
        return
    db.session.execute(
        text("ALTER TABLE customer_settings ADD COLUMN spam_action_prefs TEXT")
    )
    db.session.commit()


def ensure_import_request_takeout_columns():
    inspector = inspect(db.engine)
    if "import_requests" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("import_requests")}
    statements = []
    if "staged_upload_path" not in columns:
        statements.append("ALTER TABLE import_requests ADD COLUMN staged_upload_path VARCHAR(512)")
    if "upload_filename" not in columns:
        statements.append("ALTER TABLE import_requests ADD COLUMN upload_filename VARCHAR(255)")
    if "upload_size_bytes" not in columns:
        statements.append("ALTER TABLE import_requests ADD COLUMN upload_size_bytes INTEGER NOT NULL DEFAULT 0")
    if "uploaded_bytes" not in columns:
        statements.append("ALTER TABLE import_requests ADD COLUMN uploaded_bytes INTEGER NOT NULL DEFAULT 0")
    if "upload_status" not in columns:
        statements.append("ALTER TABLE import_requests ADD COLUMN upload_status VARCHAR(32) NOT NULL DEFAULT 'none'")
    if "upload_completed_at" not in columns:
        statements.append("ALTER TABLE import_requests ADD COLUMN upload_completed_at DATETIME")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()


def ensure_domain_caldav_columns():
    inspector = inspect(db.engine)
    if "domains" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("domains")}
    statements = []
    if "caldav_host" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN caldav_host VARCHAR(255)")
    if "caldav_port" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN caldav_port INTEGER DEFAULT 5232")
    if "caldav_use_tls" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN caldav_use_tls BOOLEAN DEFAULT 0")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()


def ensure_domain_carddav_columns():
    inspector = inspect(db.engine)
    if "domains" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("domains")}
    statements = []
    if "carddav_host" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN carddav_host VARCHAR(255)")
    if "carddav_port" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN carddav_port INTEGER DEFAULT 5232")
    if "carddav_use_tls" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN carddav_use_tls BOOLEAN DEFAULT 0")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()


def ensure_api_columns():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if "customer_accounts" in tables:
        columns = {col["name"] for col in inspector.get_columns("customer_accounts")}
        statements = []
        if "api_enabled" not in columns:
            statements.append("ALTER TABLE customer_accounts ADD COLUMN api_enabled BOOLEAN NOT NULL DEFAULT 0")
        if "dek_wrapped_cred" not in columns:
            statements.append("ALTER TABLE customer_accounts ADD COLUMN dek_wrapped_cred BLOB")
        for statement in statements:
            db.session.execute(text(statement))
        if statements:
            db.session.commit()


def ensure_oauth_token_dek():
    inspector = inspect(db.engine)
    if "oauth_access_tokens" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("oauth_access_tokens")}
    if "wrapped_dek" not in columns:
        db.session.execute(text("ALTER TABLE oauth_access_tokens ADD COLUMN wrapped_dek BLOB"))
        db.session.commit()


def ensure_customer_signup_columns():
    inspector = inspect(db.engine)
    if "customer_accounts" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("customer_accounts")}
    statements = []
    if "signup_token" not in columns:
        statements.append("ALTER TABLE customer_accounts ADD COLUMN signup_token VARCHAR(128)")
    if "signup_expires_at" not in columns:
        statements.append("ALTER TABLE customer_accounts ADD COLUMN signup_expires_at DATETIME")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()


def ensure_domain_mail_api_columns():
    inspector = inspect(db.engine)
    if "domains" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("domains")}
    statements = []
    if "mail_api_url" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN mail_api_url VARCHAR(512)")
    if "mail_api_key" not in columns:
        statements.append("ALTER TABLE domains ADD COLUMN mail_api_key VARCHAR(255)")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()


def ensure_domain_dns_config_columns():
    inspector = inspect(db.engine)
    if "domain_dns_config" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("domain_dns_config")}
    statements = []
    if "dkim_selector" not in columns:
        statements.append("ALTER TABLE domain_dns_config ADD COLUMN dkim_selector VARCHAR(64) NOT NULL DEFAULT 'default'")
    if "dmarc_policy" not in columns:
        statements.append("ALTER TABLE domain_dns_config ADD COLUMN dmarc_policy VARCHAR(16) NOT NULL DEFAULT 'none'")
    if "dmarc_rua" not in columns:
        statements.append("ALTER TABLE domain_dns_config ADD COLUMN dmarc_rua VARCHAR(255)")
    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()
