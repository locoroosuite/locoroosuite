from flask import g

from app.api.openapi import create_api_blueprint
from app.api.controllers.helpers import api_response, require_api_token
from app.api.schemas.common import ErrorResponse, AccountIdQuery
from app.api.schemas.accounts import AccountListResponse
from app.shared.models.core import CustomerAccount

bp = create_api_blueprint("accounts", "Account management")


@bp.get("/accounts", summary="List accounts", description="Returns all active mail accounts for the authenticated customer.", responses={"200": AccountListResponse, "401": ErrorResponse})
@require_api_token()
def list_accounts(query: AccountIdQuery):
    customer_id = g.api_context["customer_id"]
    accounts = CustomerAccount.query.filter_by(
        customer_id=customer_id, is_active=True
    ).all()
    items = []
    for a in accounts:
        items.append({
            "id": a.id,
            "email": a.email_address,
            "auth_type": a.auth_type,
        })
    return api_response(items)
