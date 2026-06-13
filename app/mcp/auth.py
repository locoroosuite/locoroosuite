from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import time
from typing import Any

import jwt as pyjwt
from flask import Flask, g
from mcp.server.fastmcp import FastMCP

from app.shared.db import db
from app.shared.models.core import ApiToken, ApiRateLimitConfig, CustomerAccount
from app.shared.models.oauth import OAuthAccessToken

_logger = logging.getLogger(__name__)

JWT_ALGORITHM = "RS256"

_current_auth_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_auth_token", default=""
)

_current_request_host: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_request_host", default=""
)


def set_current_token(token: str) -> None:
    _current_auth_token.set(token)


def get_current_token() -> str:
    return _current_auth_token.get()


def set_current_request_host(host: str) -> None:
    _current_request_host.set(host)


def get_current_request_host() -> str:
    return _current_request_host.get()


class McpAuthError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def setup_auth(mcp: FastMCP, flask_app: Flask) -> None:
    pass


_RATE_LIMIT_STORE: dict[str, dict[str, Any]] = {}


def _normalize_scopes(raw_scopes: list[str]) -> list[str]:
    normalized: list[str] = []
    for s in raw_scopes:
        if "." in s:
            normalized.append(s.replace(".", ":"))
        elif s in ("mail", "contacts", "calendar", "docs"):
            normalized.append(f"{s}:read")
            normalized.append(f"{s}:write")
        else:
            normalized.append(s)
    return list(dict.fromkeys(normalized))


def resolve_context(token_str: str, flask_app: Flask) -> dict[str, Any]:
    if token_str.startswith("lr_"):
        return _resolve_api_key(token_str, flask_app)
    return _resolve_jwt(token_str, flask_app)


def _resolve_api_key(raw_token: str, flask_app: Flask) -> dict[str, Any]:
    from app.api.token_service import authenticate_token

    with flask_app.app_context():
        token, ctx = authenticate_token(raw_token)
        if not token or not ctx:
            raise McpAuthError("AUTH_INVALID", "Invalid or expired API token")
        return {
            "customer_id": ctx["customer_id"],
            "dek": ctx["dek"],
            "scopes": ctx["scopes"],
            "token_type": "api_key",
        }


def _resolve_jwt(token_str: str, flask_app: Flask) -> dict[str, Any]:
    try:
        from app.shared.oauth import get_public_key

        with flask_app.app_context():
            public_key_pem = get_public_key(flask_app)
            if not public_key_pem:
                raise McpAuthError("AUTH_INVALID", "OAuth not configured")

        unverified = pyjwt.decode(token_str, options={"verify_signature": False})
        audience = unverified.get("aud")

        payload = pyjwt.decode(
            token_str,
            public_key_pem,
            algorithms=[JWT_ALGORITHM],
            audience=audience,
        )

        exp = payload.get("exp")
        if exp and exp < time.time():
            raise McpAuthError("AUTH_INVALID", "Token expired")

        customer_id = int(payload["sub"])
        scope_str = payload.get("scope", "")
        raw_scopes = scope_str.split() if scope_str else []
        scopes = _normalize_scopes(raw_scopes)

        with flask_app.app_context():
            account = CustomerAccount.query.filter_by(
                customer_id=customer_id, is_active=True
            ).first()
            if not account:
                raise McpAuthError("NO_ACCOUNT", "No active account found")

            if not account.api_enabled:
                raise McpAuthError(
                    "API_DISABLED",
                    "API access must be enabled in Settings before connecting external apps.",
                )

            from app.shared.models.core import User
            user = db.session.get(User, customer_id)
            if not user or not user.is_active:
                raise McpAuthError("AUTH_INVALID", "User not found or inactive")

        return {
            "customer_id": customer_id,
            "scopes": scopes,
            "token_type": "jwt",
            "jti": payload.get("jti"),
        }

    except pyjwt.InvalidTokenError as e:
        raise McpAuthError("AUTH_INVALID", f"Invalid OAuth token: {e}")
    except McpAuthError:
        raise
    except Exception as e:
        _logger.exception("unexpected error resolving JWT")
        raise McpAuthError("AUTH_INVALID", "Token verification failed")


def get_account_id(context: dict[str, Any], flask_app: Flask, account_id: int | None = None) -> int:
    with flask_app.app_context():
        if account_id:
            account = CustomerAccount.query.filter_by(
                id=account_id,
                customer_id=context["customer_id"],
                is_active=True,
            ).first()
            if not account:
                raise McpAuthError("NOT_FOUND", f"Account {account_id} not found")
            return account_id

        account = CustomerAccount.query.filter_by(
            customer_id=context["customer_id"], is_active=True
        ).first()
        if not account:
            raise McpAuthError("NO_ACCOUNT", "No active account found")
        return account.id


def get_dek(context: dict[str, Any], flask_app: Flask) -> str:
    if "dek" in context:
        return context["dek"]

    with flask_app.app_context():
        account = CustomerAccount.query.filter_by(
            customer_id=context["customer_id"], is_active=True
        ).first()
        if not account or not account.dek_wrapped_cred:
            raise McpAuthError("NO_DEK", "No encryption key available")

        jti = context.get("jti")
        raw_token = get_current_token()
        if not jti or not raw_token:
            raise McpAuthError("NO_DEK", "DEK not available for JWT tokens")

        token_record = OAuthAccessToken.query.filter_by(jti=jti, revoked=False).first()
        if not token_record or not token_record.wrapped_dek:
            raise McpAuthError("NO_DEK", "No DEK stored for this OAuth token")

        from app.api.token_service import unwrap_dek_from_token
        try:
            dek_hex = unwrap_dek_from_token(token_record.wrapped_dek, raw_token.encode())
        except Exception:
            raise McpAuthError("NO_DEK", "Failed to unwrap DEK")

        return dek_hex


def require_scope(context: dict[str, Any], module: str, access: str = "read") -> None:
    scopes = context.get("scopes", [])
    if access == "write":
        if f"{module}:write" not in scopes:
            raise McpAuthError(
                "SCOPE_DENIED",
                f"This action requires the '{module}:write' permission.",
            )
    else:
        if not any(s.startswith(f"{module}:") for s in scopes):
            raise McpAuthError(
                "SCOPE_DENIED",
                f"This action requires the '{module}:read' permission.",
            )


class _FlaskAppContext:
    def __init__(self):
        from app import create_app
        self._app = create_app()
        self._ctx = self._app.app_context()

    def __enter__(self):
        self._ctx.push()
        return self

    def __exit__(self, *args):
        self._ctx.pop()


def check_rate_limit(token_id: int, flask_app: Flask) -> bool:
    with flask_app.app_context():
        rate_limit = 60
        try:
            config = db.session.query(ApiRateLimitConfig).first()
            if config:
                rate_limit = config.default_requests_per_minute
        except Exception:
            pass

    window = 60
    now = time.time()
    key = f"mcp_rl:{token_id}"
    bucket = _RATE_LIMIT_STORE.get(key)
    if bucket is None or now - bucket["start"] > window:
        bucket = {"start": now, "count": 0}
        _RATE_LIMIT_STORE[key] = bucket
    bucket["count"] += 1
    return bucket["count"] <= rate_limit
