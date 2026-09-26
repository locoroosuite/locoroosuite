"""Versioned schema migrations for the main application database (SQLAlchemy / sqlite3).

Previously the main DB used 12 ad-hoc ``ensure_*`` functions in
``db_migrations.py``, each called individually from the app factory. This
consolidates them into the unified migration runner so they are versioned,
auditable, and applied in a single ordered pass.

Each migration is self-guarding (checks column existence before altering) and
accepts a raw DBAPI connection (from ``db.engine.raw_connection()``). They run
after ``db.create_all()`` in the app factory, so all model-defined tables
already exist; the guards are a safety net for partial-creation edge cases.
"""

from __future__ import annotations

from app.shared.migrations import Migration, has_table, table_columns


def _domain_status(conn) -> None:
    if not has_table(conn, "domains"):
        return
    if "status" in table_columns(conn, "domains"):
        return
    conn.execute("ALTER TABLE domains ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'draft'")
    rows = conn.execute(
        "SELECT id, imap_host, imap_port, smtp_host, smtp_port, smtp_tls_mode FROM domains"
    ).fetchall()
    for row in rows:
        domain_id = row[0]
        imap_host, imap_port = row[1], row[2]
        smtp_host, smtp_port, smtp_tls_mode = row[3], row[4], row[5]
        status = "complete"
        if not imap_host or not imap_port:
            status = "review"
        if not smtp_host or not smtp_port or not smtp_tls_mode:
            status = "review"
        conn.execute("UPDATE domains SET status = ? WHERE id = ?", (status, domain_id))


def _customer_settings_spam_action(conn) -> None:
    if not has_table(conn, "customer_settings"):
        return
    if "spam_action_prefs" in table_columns(conn, "customer_settings"):
        return
    conn.execute("ALTER TABLE customer_settings ADD COLUMN spam_action_prefs TEXT")


def _customer_settings_protection(conn) -> None:
    if not has_table(conn, "customer_settings"):
        return
    cols = table_columns(conn, "customer_settings")
    if "protected_folders" not in cols:
        conn.execute("ALTER TABLE customer_settings ADD COLUMN protected_folders TEXT")
    if "protect_starred" not in cols:
        conn.execute(
            "ALTER TABLE customer_settings ADD COLUMN protect_starred BOOLEAN NOT NULL DEFAULT 1"
        )
    if "locked_keyword_prefs" not in cols:
        conn.execute("ALTER TABLE customer_settings ADD COLUMN locked_keyword_prefs TEXT")


def _import_request_takeout(conn) -> None:
    if not has_table(conn, "import_requests"):
        return
    cols = table_columns(conn, "import_requests")
    if "staged_upload_path" not in cols:
        conn.execute("ALTER TABLE import_requests ADD COLUMN staged_upload_path VARCHAR(512)")
    if "upload_filename" not in cols:
        conn.execute("ALTER TABLE import_requests ADD COLUMN upload_filename VARCHAR(255)")
    if "upload_size_bytes" not in cols:
        conn.execute(
            "ALTER TABLE import_requests ADD COLUMN upload_size_bytes INTEGER NOT NULL DEFAULT 0"
        )
    if "uploaded_bytes" not in cols:
        conn.execute(
            "ALTER TABLE import_requests ADD COLUMN uploaded_bytes INTEGER NOT NULL DEFAULT 0"
        )
    if "upload_status" not in cols:
        conn.execute(
            "ALTER TABLE import_requests ADD COLUMN upload_status VARCHAR(32) NOT NULL DEFAULT 'none'"
        )
    if "upload_completed_at" not in cols:
        conn.execute("ALTER TABLE import_requests ADD COLUMN upload_completed_at DATETIME")


def _domain_caldav(conn) -> None:
    if not has_table(conn, "domains"):
        return
    cols = table_columns(conn, "domains")
    if "caldav_host" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN caldav_host VARCHAR(255)")
    if "caldav_port" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN caldav_port INTEGER DEFAULT 5232")
    if "caldav_use_tls" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN caldav_use_tls BOOLEAN DEFAULT 0")


def _domain_carddav(conn) -> None:
    if not has_table(conn, "domains"):
        return
    cols = table_columns(conn, "domains")
    if "carddav_host" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN carddav_host VARCHAR(255)")
    if "carddav_port" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN carddav_port INTEGER DEFAULT 5232")
    if "carddav_use_tls" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN carddav_use_tls BOOLEAN DEFAULT 0")


def _domain_matrix(conn) -> None:
    if not has_table(conn, "domains"):
        return
    cols = table_columns(conn, "domains")
    if "matrix_host" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN matrix_host VARCHAR(255)")
    if "matrix_port" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN matrix_port INTEGER DEFAULT 8008")
    if "matrix_use_tls" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN matrix_use_tls BOOLEAN DEFAULT 0")
    if "matrix_shared_secret" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN matrix_shared_secret VARCHAR(255)")
    if has_table(conn, "customer_accounts") and "chat_encrypted_secret" not in table_columns(
        conn, "customer_accounts"
    ):
        conn.execute("ALTER TABLE customer_accounts ADD COLUMN chat_encrypted_secret BLOB")


def _api_columns(conn) -> None:
    if not has_table(conn, "customer_accounts"):
        return
    cols = table_columns(conn, "customer_accounts")
    if "api_enabled" not in cols:
        conn.execute(
            "ALTER TABLE customer_accounts ADD COLUMN api_enabled BOOLEAN NOT NULL DEFAULT 0"
        )
    if "dek_wrapped_cred" not in cols:
        conn.execute("ALTER TABLE customer_accounts ADD COLUMN dek_wrapped_cred BLOB")


def _oauth_token_dek(conn) -> None:
    if not has_table(conn, "oauth_access_tokens"):
        return
    if "wrapped_dek" in table_columns(conn, "oauth_access_tokens"):
        return
    conn.execute("ALTER TABLE oauth_access_tokens ADD COLUMN wrapped_dek BLOB")


def _customer_signup(conn) -> None:
    if not has_table(conn, "customer_accounts"):
        return
    cols = table_columns(conn, "customer_accounts")
    if "signup_token" not in cols:
        conn.execute("ALTER TABLE customer_accounts ADD COLUMN signup_token VARCHAR(128)")
    if "signup_expires_at" not in cols:
        conn.execute("ALTER TABLE customer_accounts ADD COLUMN signup_expires_at DATETIME")


def _domain_mail_api(conn) -> None:
    if not has_table(conn, "domains"):
        return
    cols = table_columns(conn, "domains")
    if "mail_api_url" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN mail_api_url VARCHAR(512)")
    if "mail_api_key" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN mail_api_key VARCHAR(255)")


def _domain_dns_config(conn) -> None:
    if not has_table(conn, "domain_dns_config"):
        return
    cols = table_columns(conn, "domain_dns_config")
    if "dkim_selector" not in cols:
        conn.execute(
            "ALTER TABLE domain_dns_config ADD COLUMN dkim_selector VARCHAR(64) NOT NULL DEFAULT 'default'"
        )
    if "dmarc_policy" not in cols:
        conn.execute(
            "ALTER TABLE domain_dns_config ADD COLUMN dmarc_policy VARCHAR(16) NOT NULL DEFAULT 'none'"
        )
    if "dmarc_rua" not in cols:
        conn.execute("ALTER TABLE domain_dns_config ADD COLUMN dmarc_rua VARCHAR(255)")


def _user_totp(conn) -> None:
    if not has_table(conn, "users"):
        return
    cols = table_columns(conn, "users")
    if "totp_secret" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)")
    if "totp_enabled" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT 0")
    if "backup_codes" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN backup_codes TEXT")


def _push_notifications(conn) -> None:
    if not has_table(conn, "push_subscriptions"):
        conn.execute(
            """
            CREATE TABLE push_subscriptions (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                endpoint VARCHAR(512) NOT NULL UNIQUE,
                p256dh VARCHAR(255) NOT NULL,
                auth VARCHAR(255) NOT NULL,
                user_agent VARCHAR(255),
                created_at DATETIME NOT NULL,
                last_used_at DATETIME,
                disabled_at DATETIME
            )
            """
        )
        conn.execute("CREATE INDEX ix_push_subscriptions_user_id ON push_subscriptions (user_id)")
    if not has_table(conn, "push_vapid_keys"):
        conn.execute(
            """
            CREATE TABLE push_vapid_keys (
                id INTEGER NOT NULL PRIMARY KEY,
                public_key VARCHAR(255) NOT NULL UNIQUE,
                private_key TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
    if has_table(conn, "customer_settings") and "push_detailed" not in table_columns(
        conn, "customer_settings"
    ):
        conn.execute(
            "ALTER TABLE customer_settings ADD COLUMN push_detailed BOOLEAN NOT NULL DEFAULT 0"
        )


def _notification_prefs(conn) -> None:
    if has_table(conn, "customer_settings"):
        cols = table_columns(conn, "customer_settings")
        if "notify_mail_enabled" not in cols:
            conn.execute(
                "ALTER TABLE customer_settings ADD COLUMN notify_mail_enabled BOOLEAN NOT NULL DEFAULT 1"
            )
        if "notify_calendar_enabled" not in cols:
            conn.execute(
                "ALTER TABLE customer_settings ADD COLUMN notify_calendar_enabled BOOLEAN NOT NULL DEFAULT 0"
            )
    if not has_table(conn, "push_key_store"):
        conn.execute(
            """
            CREATE TABLE push_key_store (
                customer_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(id),
                wrapped_dek BLOB NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
    if not has_table(conn, "push_calendar_fired"):
        conn.execute(
            """
            CREATE TABLE push_calendar_fired (
                id INTEGER NOT NULL PRIMARY KEY,
                account_id INTEGER NOT NULL,
                event_uid VARCHAR(255) NOT NULL,
                trigger_val VARCHAR(64) NOT NULL,
                fired_at DATETIME NOT NULL,
                CONSTRAINT uq_push_cal_fired UNIQUE (account_id, event_uid, trigger_val)
            )
            """
        )


def _customer_settings_language(conn) -> None:
    if not has_table(conn, "customer_settings"):
        return
    if "language" in table_columns(conn, "customer_settings"):
        return
    conn.execute(
        "ALTER TABLE customer_settings ADD COLUMN language VARCHAR(16) NOT NULL DEFAULT 'browser'"
    )


def _customer_settings_browser_locale(conn) -> None:
    if not has_table(conn, "customer_settings"):
        return
    if "browser_locale" in table_columns(conn, "customer_settings"):
        return
    conn.execute("ALTER TABLE customer_settings ADD COLUMN browser_locale VARCHAR(8)")


APP_DB_MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001_domain_status", _domain_status),
    Migration("0002_customer_settings_spam_action", _customer_settings_spam_action),
    Migration("0003_customer_settings_protection", _customer_settings_protection),
    Migration("0004_import_request_takeout", _import_request_takeout),
    Migration("0005_domain_carddav", _domain_carddav),
    Migration("0006_domain_caldav", _domain_caldav),
    Migration("0007_api_columns", _api_columns),
    Migration("0008_oauth_token_dek", _oauth_token_dek),
    Migration("0009_customer_signup", _customer_signup),
    Migration("0010_domain_mail_api", _domain_mail_api),
    Migration("0011_domain_dns_config", _domain_dns_config),
    Migration("0012_user_totp", _user_totp),
    Migration("0013_push_notifications", _push_notifications),
    Migration("0014_domain_matrix", _domain_matrix),
    Migration("0015_notification_prefs", _notification_prefs),
    Migration("0016_customer_settings_language", _customer_settings_language),
    Migration("0017_customer_settings_browser_locale", _customer_settings_browser_locale),
)
