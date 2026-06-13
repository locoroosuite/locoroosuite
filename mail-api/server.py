from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from flask import Flask, request, jsonify, g
from managers.dovecot import DovecotManager
from managers.opendkim import OpenDKIMManager
from managers.postfix import PostfixManager

app = Flask(__name__)
app.config.from_envvar("MAIL_API_SETTINGS", silent=True)

logger = logging.getLogger(__name__)

dovecot = DovecotManager(
    users_path=app.config.get("DOVECOT_USERS_PATH", "/etc/dovecot/users"),
    mail_root=app.config.get("DOVECOT_MAIL_ROOT", "/var/mail/vhosts"),
)
postfix = PostfixManager(
    domains_path=app.config.get("POSTFIX_DOMAINS_PATH", "/etc/postfix/virtual_domains"),
    aliases_path=app.config.get("POSTFIX_ALIASES_PATH", "/etc/postfix/virtual"),
    aliases_db_path=app.config.get("POSTFIX_ALIASES_DB_PATH", "/etc/postfix/virtual.db"),
)
opendkim = OpenDKIMManager(
    keys_dir=app.config.get("OPENDKIM_KEYS_DIR", "/etc/opendkim/keys"),
    key_table_path=app.config.get("OPENDKIM_KEY_TABLE", "/etc/opendkim/key-table"),
    signing_table_path=app.config.get("OPENDKIM_SIGNING_TABLE", "/etc/opendkim/signing-table"),
    selector=app.config.get("OPENDKIM_SELECTOR", "default"),
)

SENDING_LIMITS_DB = app.config.get("SENDING_LIMITS_DB", "/var/lib/mail-api/sending_limits.db")

API_KEY = app.config.get("MAIL_API_KEY", "")


def _check_auth():
    if not API_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == API_KEY
    return False


def _auth_error():
    return jsonify({"error": {"code": "UNAUTHORIZED", "message": "Invalid or missing API key"}}), 401


def _ok(data=None, status=200):
    body = {"status": "ok"}
    if data is not None:
        body.update(data)
    return jsonify(body), status


def _error(code, message, status=400):
    return jsonify({"error": {"code": code, "message": message}}), status


@app.route("/health", methods=["GET"])
def health():
    return _ok({"service": "mail-api"})


@app.route("/api/domains", methods=["GET"])
def list_domains():
    if not _check_auth():
        return _auth_error()
    domains = postfix.list_domains()
    return jsonify({"data": domains})


@app.route("/api/domains", methods=["POST"])
def add_domain():
    if not _check_auth():
        return _auth_error()
    body = request.get_json(silent=True) or {}
    domain_name = body.get("domain", "").strip().lower()
    if not domain_name:
        return _error("VALIDATION_ERROR", "domain is required")
    try:
        postfix.add_domain(domain_name)
        return _ok({"domain": domain_name}, 201)
    except Exception as exc:
        return _error("DOMAIN_ADD_FAILED", str(exc), 500)


@app.route("/api/domains/<domain_name>", methods=["DELETE"])
def remove_domain(domain_name):
    if not _check_auth():
        return _auth_error()
    try:
        postfix.remove_domain(domain_name.strip().lower())
        return _ok({"domain": domain_name.strip().lower()})
    except Exception as exc:
        return _error("DOMAIN_REMOVE_FAILED", str(exc), 500)


@app.route("/api/users", methods=["GET"])
def list_users():
    if not _check_auth():
        return _auth_error()
    domain = request.args.get("domain", "").strip().lower()
    users = dovecot.list_users(domain)
    return jsonify({"data": users})


@app.route("/api/users", methods=["POST"])
def add_user():
    if not _check_auth():
        return _auth_error()
    body = request.get_json(silent=True) or {}
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    if not email or not password:
        return _error("VALIDATION_ERROR", "email and password are required")
    if "@" not in email:
        return _error("VALIDATION_ERROR", "email must contain @")
    try:
        dovecot.add_user(email, password, quota_bytes=body.get("quota_bytes"))
        return _ok({"email": email}, 201)
    except FileExistsError:
        return _error("USER_EXISTS", f"User {email} already exists", 409)
    except Exception as exc:
        return _error("USER_ADD_FAILED", str(exc), 500)


@app.route("/api/users/<email>", methods=["DELETE"])
def remove_user(email):
    if not _check_auth():
        return _auth_error()
    email = email.strip().lower()
    try:
        dovecot.remove_user(email)
        return _ok({"email": email})
    except FileNotFoundError:
        return _error("USER_NOT_FOUND", f"User {email} not found", 404)
    except Exception as exc:
        return _error("USER_REMOVE_FAILED", str(exc), 500)


@app.route("/api/users/<email>/password", methods=["PUT"])
def set_password(email):
    if not _check_auth():
        return _auth_error()
    email = email.strip().lower()
    body = request.get_json(silent=True) or {}
    password = body.get("password", "")
    if not password:
        return _error("VALIDATION_ERROR", "password is required")
    try:
        dovecot.set_password(email, password)
        return _ok({"email": email})
    except FileNotFoundError:
        return _error("USER_NOT_FOUND", f"User {email} not found", 404)
    except Exception as exc:
        return _error("PASSWORD_SET_FAILED", str(exc), 500)


@app.route("/api/users/<email>/check", methods=["GET"])
def check_user(email):
    if not _check_auth():
        return _auth_error()
    email = email.strip().lower()
    exists = dovecot.user_exists(email)
    if exists:
        return _ok({"email": email, "exists": True})
    return _error("USER_NOT_FOUND", f"User {email} not found", 404)


@app.route("/api/dkim/<domain_name>", methods=["GET"])
def get_dkim(domain_name):
    if not _check_auth():
        return _auth_error()
    selector = request.args.get("selector") or None
    try:
        key_data = opendkim.get_key(domain_name.strip().lower(), selector=selector)
        return _ok({"dkim": key_data})
    except FileNotFoundError:
        return _error("DKIM_NOT_FOUND", f"No DKIM key found for {domain_name}", 404)
    except Exception as exc:
        return _error("DKIM_GET_FAILED", str(exc), 500)


@app.route("/api/dkim/<domain_name>", methods=["POST"])
def generate_dkim(domain_name):
    if not _check_auth():
        return _auth_error()
    body = request.get_json(silent=True) or {}
    selector = body.get("selector") or None
    try:
        key_data = opendkim.generate_key(domain_name.strip().lower(), selector=selector)
        return _ok({"dkim": key_data}, 201)
    except Exception as exc:
        return _error("DKIM_GENERATE_FAILED", str(exc), 500)


@app.route("/api/dkim/<domain_name>", methods=["DELETE"])
def remove_dkim(domain_name):
    if not _check_auth():
        return _auth_error()
    body = request.get_json(silent=True) or {}
    selector = body.get("selector") or None
    try:
        opendkim.remove_key(domain_name.strip().lower(), selector=selector)
        return _ok({"domain": domain_name.strip().lower()})
    except Exception as exc:
        return _error("DKIM_REMOVE_FAILED", str(exc), 500)


def _get_sending_db():
    db = getattr(g, "_sending_db", None)
    if db is None:
        import os
        db_dir = os.path.dirname(SENDING_LIMITS_DB)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        db = sqlite3.connect(SENDING_LIMITS_DB)
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE IF NOT EXISTS sending_limits "
            "(email TEXT PRIMARY KEY, max_per_day INTEGER NOT NULL, "
            "sent_today INTEGER DEFAULT 0, last_reset_date TEXT)"
        )
        db.commit()
        g._sending_db = db
    return db


@app.teardown_appcontext
def _close_sending_db(exc):
    db = getattr(g, "_sending_db", None)
    if db is not None:
        db.close()


@app.route("/api/users/<email>/quota", methods=["PUT"])
def set_quota(email):
    if not _check_auth():
        return _auth_error()
    email = email.strip().lower()
    body = request.get_json(silent=True) or {}
    quota_bytes = body.get("quota_bytes")
    if quota_bytes is None or not isinstance(quota_bytes, int) or quota_bytes < 0:
        return _error("VALIDATION_ERROR", "quota_bytes must be a non-negative integer")
    try:
        dovecot.set_quota(email, quota_bytes)
        return _ok({"email": email, "quota_bytes": quota_bytes})
    except FileNotFoundError:
        return _error("USER_NOT_FOUND", f"User {email} not found", 404)
    except Exception as exc:
        return _error("QUOTA_SET_FAILED", str(exc), 500)


@app.route("/api/users/<email>/sending-limit", methods=["POST"])
def set_sending_limit(email):
    if not _check_auth():
        return _auth_error()
    email = email.strip().lower()
    body = request.get_json(silent=True) or {}
    max_per_day = body.get("max_per_day")
    if max_per_day is None or not isinstance(max_per_day, int) or max_per_day < 0:
        return _error("VALIDATION_ERROR", "max_per_day must be a non-negative integer")
    today = datetime.now(timezone.utc).date().isoformat()
    db = _get_sending_db()
    db.execute(
        "INSERT INTO sending_limits (email, max_per_day, sent_today, last_reset_date) "
        "VALUES (?, ?, 0, ?) ON CONFLICT(email) DO UPDATE SET max_per_day=?",
        (email, max_per_day, today, max_per_day),
    )
    db.commit()
    return _ok({"email": email, "max_per_day": max_per_day}, 201)


@app.route("/api/users/<email>/sending-limit", methods=["GET"])
def get_sending_limit(email):
    if not _check_auth():
        return _auth_error()
    email = email.strip().lower()
    db = _get_sending_db()
    row = db.execute(
        "SELECT email, max_per_day, sent_today, last_reset_date FROM sending_limits WHERE email=?",
        (email,),
    ).fetchone()
    if not row:
        return _error("NOT_FOUND", f"No sending limit configured for {email}", 404)
    return _ok({
        "email": row["email"],
        "max_per_day": row["max_per_day"],
        "sent_today": row["sent_today"],
        "last_reset_date": row["last_reset_date"],
    })


@app.route("/api/users/<email>/sending-limit", methods=["DELETE"])
def delete_sending_limit(email):
    if not _check_auth():
        return _auth_error()
    email = email.strip().lower()
    db = _get_sending_db()
    db.execute("DELETE FROM sending_limits WHERE email=?", (email,))
    db.commit()
    return _ok({"email": email})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8800, debug=True)
