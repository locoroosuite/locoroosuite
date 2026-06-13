import time
from functools import wraps

from flask import request, g, current_app

from app.shared.db import db
from app.shared.models.core import ApiRateLimitConfig


class ApiError(Exception):
    def __init__(self, code, message, status=400, details=None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def api_response(data=None, status=200):
    body = {}
    if data is not None:
        body["data"] = data
    return body, status


def api_paginated(items, next_cursor=None, has_more=False):
    return {
        "data": items,
        "pagination": {"next_cursor": next_cursor, "has_more": has_more},
    }


def api_error(code, message, status=400, details=None):
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body, status


def _authenticate_bearer(raw_token):
    from app.api.token_service import authenticate_token
    from app.shared.models.core import CustomerAccount

    if raw_token.startswith("lr_"):
        token, ctx = authenticate_token(raw_token)
        if not token:
            raise ApiError("AUTH_INVALID", "Invalid or expired token", 401)
        return token, ctx

    import jwt as pyjwt
    from app.shared.oauth import get_public_key

    app = current_app._get_current_object()
    public_key_pem = get_public_key(app)
    if not public_key_pem:
        raise ApiError("AUTH_INVALID", "OAuth not configured", 401)

    try:
        unverified = pyjwt.decode(raw_token, options={"verify_signature": False})
        audience = unverified.get("aud")
        payload = pyjwt.decode(raw_token, public_key_pem, algorithms=["RS256"], audience=audience)
    except pyjwt.InvalidTokenError:
        raise ApiError("AUTH_INVALID", "Invalid or expired token", 401)

    exp = payload.get("exp")
    if exp and exp < time.time():
        raise ApiError("AUTH_INVALID", "Token expired", 401)

    customer_id = int(payload["sub"])
    scope_str = payload.get("scope", "")
    raw_scopes = scope_str.split() if scope_str else []
    scopes = _normalize_scopes(raw_scopes)

    account = CustomerAccount.query.filter_by(customer_id=customer_id, is_active=True).first()
    if not account:
        raise ApiError("NO_ACCOUNT", "No active account found", 401)

    jti = payload.get("jti")
    dek_hex = None
    if jti:
        from app.shared.models.oauth import OAuthAccessToken
        token_record = OAuthAccessToken.query.filter_by(jti=jti, revoked=False).first()
        if token_record and token_record.wrapped_dek:
            from app.api.token_service import unwrap_dek_from_token
            try:
                dek_hex = unwrap_dek_from_token(token_record.wrapped_dek, raw_token.encode())
            except Exception:
                raise ApiError("DEK_MISMATCH", "Failed to decrypt data encryption key. Please create a new API token or reset API access in Settings.", 401)

    ctx = {
        "dek": dek_hex,
        "scopes": scopes,
        "customer_id": customer_id,
    }
    return None, ctx


def _normalize_scopes(raw_scopes):
    normalized = []
    for s in raw_scopes:
        if "." in s:
            normalized.append(s.replace(".", ":"))
        elif s in ("mail", "contacts", "calendar", "docs"):
            normalized.append(f"{s}:read")
            normalized.append(f"{s}:write")
        else:
            normalized.append(s)
    return list(dict.fromkeys(normalized))


def require_api_token(scopes=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return api_error("AUTH_MISSING", "Authorization header with Bearer token required", 401)
            raw_token = auth_header[7:]

            try:
                token_obj, ctx = _authenticate_bearer(raw_token)
            except ApiError as e:
                return api_error(e.code, e.message, e.status)

            g.api_token = token_obj
            g.api_context = ctx

            if scopes:
                token_scopes = ctx.get("scopes", [])
                for required in scopes:
                    module = required.split(":")[0]
                    access = required.split(":")[1] if ":" in required else None
                    has_module = any(s.startswith(module + ":") or s == module for s in token_scopes)
                    if not has_module:
                        return api_error(
                            "SCOPE_DENIED",
                            f"This action requires the '{required}' permission.",
                            403,
                        )
                    if access == "write":
                        has_write = any(s == f"{module}:write" for s in token_scopes)
                        if not has_write:
                            return api_error(
                                "SCOPE_DENIED",
                                f"This action requires the '{module}:write' permission.",
                                403,
                            )

            if token_obj and not _check_rate_limit(token_obj):
                return api_error("RATE_LIMITED", "Rate limit exceeded", 429)

            return f(*args, **kwargs)
        return wrapper
    end_decorator = decorator
    return end_decorator


def require_scope(module, access="read"):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ctx = g.get("api_context", {})
            token_scopes = ctx.get("scopes", [])
            if access == "write":
                has_access = any(s == f"{module}:write" for s in token_scopes)
            else:
                has_access = any(
                    s.startswith(f"{module}:") for s in token_scopes
                )
            if not has_access:
                scope_name = f"{module}:{access}"
                return api_error(
                    "SCOPE_DENIED",
                    f"This action requires the '{scope_name}' permission.",
                    403,
                )
            return f(*args, **kwargs)
        return wrapper
    return decorator


_rate_limit_store = {}


def _check_rate_limit(token):
    rate_limit = _get_rate_limit()
    window = 60
    now = time.time()
    key = f"api_rl:{token.id}"
    bucket = _rate_limit_store.get(key)
    if bucket is None or now - bucket["start"] > window:
        bucket = {"start": now, "count": 0}
        _rate_limit_store[key] = bucket
    bucket["count"] += 1
    if bucket["count"] > rate_limit:
        return False
    return True


def _get_rate_limit():
    try:
        config = db.session.query(ApiRateLimitConfig).first()
        if config:
            return config.default_requests_per_minute
    except Exception:
        pass
    return current_app.config.get("API_RATE_LIMIT_PER_MINUTE", 60)


def get_api_account_id():
    account_id = request.args.get("account_id")
    if not account_id and request.is_json:
        try:
            body = request.get_json(silent=True, force=True)
            if body and isinstance(body, dict):
                account_id = body.get("account_id")
        except Exception:
            pass
    if account_id:
        return int(account_id)
    from app.shared.models.core import CustomerAccount
    customer_id = g.api_context["customer_id"]
    account = CustomerAccount.query.filter_by(
        customer_id=customer_id, is_active=True
    ).first()
    if not account:
        raise ApiError("NO_ACCOUNT", "No active account found", 404)
    return account.id
