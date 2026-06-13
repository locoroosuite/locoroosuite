from __future__ import annotations

from pydantic import BaseModel, Field


class AccountQuery(BaseModel):
    account_id: int | None = Field(default=None, description="Mail account ID")


class AccountItem(BaseModel):
    id: int = Field(..., description="Account ID")
    email: str = Field(..., description="Email address")
    auth_type: str = Field(..., description="Authentication type")


class AccountListResponse(BaseModel):
    data: list[AccountItem] = Field(..., description="List of accounts")


class TokenItem(BaseModel):
    id: int = Field(..., description="Token ID")
    name: str = Field(..., description="Human-readable token name")
    scopes: list[str] = Field(..., description="Authorized scopes (e.g. mail:read, contacts:write)")
    created_at: str | None = Field(None, description="Creation timestamp (ISO 8601)")
    last_used_at: str | None = Field(None, description="Last usage timestamp (ISO 8601)")


class TokenListResponse(BaseModel):
    data: list[TokenItem] = Field(..., description="List of API tokens")


class TokenPath(BaseModel):
    token_id: int = Field(..., description="Token ID to revoke")
