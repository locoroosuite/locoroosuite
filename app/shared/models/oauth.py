from __future__ import annotations

from datetime import datetime, timezone

from app.shared.db import db


def _utcnow():
    return datetime.now(timezone.utc)


class OAuthClient(db.Model):
    __tablename__ = "oauth_clients"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(128), unique=True, nullable=False)
    client_name = db.Column(db.String(255), nullable=False)
    redirect_uris = db.Column(db.Text, nullable=False)
    token_endpoint_auth_method = db.Column(db.String(32), default="none")
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class OAuthAuthorizationCode(db.Model):
    __tablename__ = "oauth_auth_codes"
    id = db.Column(db.Integer, primary_key=True)
    code_hash = db.Column(db.String(64), unique=True, nullable=False)
    client_id = db.Column(db.String(128), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    redirect_uri = db.Column(db.Text, nullable=False)
    scope = db.Column(db.Text, nullable=False)
    resource = db.Column(db.Text, nullable=False)
    code_challenge = db.Column(db.String(128), nullable=False)
    code_challenge_method = db.Column(db.String(16), default="S256")
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)


class OAuthAccessToken(db.Model):
    __tablename__ = "oauth_access_tokens"
    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(128), unique=True, nullable=False)
    client_id = db.Column(db.String(128), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    scope = db.Column(db.Text, nullable=False)
    resource = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked = db.Column(db.Boolean, default=False)
    wrapped_dek = db.Column(db.LargeBinary, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
