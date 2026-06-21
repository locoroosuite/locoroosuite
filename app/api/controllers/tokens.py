import json

from flask import request, g

from app.api.openapi import create_api_blueprint
from app.api.controllers.helpers import api_response, api_error, require_api_token
from app.api.schemas.common import ErrorResponse
from app.api.schemas.accounts import TokenListResponse, TokenPath
from app.shared.models.core import ApiToken
from app.shared.audit import log_audit

bp = create_api_blueprint("tokens", "API token management")


@bp.get("/tokens", summary="List API tokens", description="Returns all API tokens for the authenticated customer, including scopes and usage timestamps. Requires `mail:read` scope.", responses={"200": TokenListResponse, "401": ErrorResponse})
@require_api_token(scopes=["mail:read"])
def list_tokens():
    customer_id = g.api_context["customer_id"]
    tokens = ApiToken.query.filter_by(customer_id=customer_id).all()
    items = []
    for t in tokens:
        items.append({
            "id": t.id,
            "name": t.name,
            "scopes": json.loads(t.scopes),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        })
    return api_response(items)


@bp.delete("/tokens/<int:token_id>", summary="Revoke API token", description="Permanently revokes an API token by ID. The token can no longer be used for authentication after revocation. Requires `mail:write` scope.", responses={"204": None, "401": ErrorResponse, "404": ErrorResponse})
@require_api_token(scopes=["mail:write"])
def revoke_token(path: TokenPath):
    customer_id = g.api_context["customer_id"]
    from app.api.token_service import revoke_api_token
    ok = revoke_api_token(path.token_id, customer_id)
    if not ok:
        return api_error("NOT_FOUND", "Token not found", 404)
    log_audit(customer_id, "customer", "api_token_revoke", f"token_id={path.token_id}",
              request.remote_addr, request.headers.get("User-Agent", ""))
    return api_response(None, 204)
