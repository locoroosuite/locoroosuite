import os
import secrets as _secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
IMPORT_UPLOAD_DIR = DATA_DIR / "import_uploads"
IMPORT_UPLOAD_DIR.mkdir(exist_ok=True)

_APP_ENV = os.environ.get("APP_ENV", "development")

if _APP_ENV == "production":
    _SECRET_KEY = os.environ.get("SECRET_KEY", "")
    _WOPI_JWT_SECRET = os.environ.get("WOPI_JWT_SECRET", "")
    if not _SECRET_KEY:
        raise RuntimeError("SECRET_KEY environment variable is required in production")
    if not _WOPI_JWT_SECRET:
        raise RuntimeError("WOPI_JWT_SECRET environment variable is required in production")
else:
    _SECRET_KEY = os.environ.get("SECRET_KEY") or _secrets.token_hex(32)
    _WOPI_JWT_SECRET = os.environ.get("WOPI_JWT_SECRET") or _secrets.token_hex(32)


class AppConfig:
    APP_ENV = _APP_ENV
    SECRET_KEY = _SECRET_KEY
    SESSION_COOKIE_NAME = "locoroomail_session"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "APP_DATABASE_URI",
        f"sqlite:///{DATA_DIR / 'app.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PREFERRED_URL_SCHEME = "https"
    IMAP_IDLE_FALLBACK_SECONDS = int(os.environ.get("IMAP_IDLE_FALLBACK_SECONDS", "60"))
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "WARNING" if APP_ENV == "production" else "INFO")
    SNIPPET_DEBUG = os.environ.get(
        "SNIPPET_DEBUG", "0" if APP_ENV == "production" else "1"
    ) == "1"
    GOOGLE_IMPORT_CLIENT_ID = os.environ.get("GOOGLE_IMPORT_CLIENT_ID", "")
    GOOGLE_IMPORT_CLIENT_SECRET = os.environ.get("GOOGLE_IMPORT_CLIENT_SECRET", "")
    GOOGLE_IMPORT_SCOPES = [
        scope.strip()
        for scope in os.environ.get(
            "GOOGLE_IMPORT_SCOPES",
            "https://www.googleapis.com/auth/gmail.readonly",
        ).split(",")
        if scope.strip()
    ]
    IMPORT_UPLOAD_RETENTION_HOURS = int(os.environ.get("IMPORT_UPLOAD_RETENTION_HOURS", "48"))
    COLLABORA_URL = os.environ.get("COLLABORA_URL", "http://localhost:9980")
    COLLABORA_INTERNAL_URL = os.environ.get("COLLABORA_INTERNAL_URL", "")
    COLLABORA_PUBLIC_URL = os.environ.get("COLLABORA_PUBLIC_URL", "")
    WOPI_HOST_URL = os.environ.get("WOPI_HOST_URL", "")
    WOPI_JWT_SECRET = _WOPI_JWT_SECRET
    API_RATE_LIMIT_PER_MINUTE = int(os.environ.get("API_RATE_LIMIT_PER_MINUTE", "60"))
    SERVER_NAME = os.environ.get("SERVER_NAME", "")
    MCP_ENABLED = os.environ.get("MCP_ENABLED", "true") == "true"
    OAUTH_SIGNING_KEY_PATH = os.environ.get(
        "OAUTH_SIGNING_KEY_PATH", str(DATA_DIR / "oauth_signing_key.pem")
    )
    MAIL_API_URL = os.environ.get("MAIL_API_URL", "")
    MAIL_API_KEY = os.environ.get("MAIL_API_KEY", "")
    APP_URL = os.environ.get("APP_URL", "http://localhost:5001")
    DOCS_DIR = os.environ.get("DOCS_DIR", str(DATA_DIR / "docs"))
    PROVISIONING_API_KEY = os.environ.get("PROVISIONING_API_KEY", "")
    MAIL_ATTACHMENTS_DIR = os.environ.get("MAIL_ATTACHMENTS_DIR", str(DATA_DIR / "mail_attachments"))
    MAIL_ATTACHMENT_MAX_FILE_BYTES = int(os.environ.get("MAIL_ATTACHMENT_MAX_FILE_BYTES", str(25 * 1024 * 1024)))
    MAIL_ATTACHMENT_MAX_TOTAL_BYTES = int(os.environ.get("MAIL_ATTACHMENT_MAX_TOTAL_BYTES", str(50 * 1024 * 1024)))
    MAIL_ATTACHMENT_STAGING_TTL_HOURS = int(os.environ.get("MAIL_ATTACHMENT_STAGING_TTL_HOURS", "24"))
