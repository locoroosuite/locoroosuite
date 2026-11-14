from __future__ import annotations

from pydantic import BaseModel, Field


class Pagination(BaseModel):
    next_cursor: str | None = Field(default=None, description="Opaque cursor for the next page")
    has_more: bool = Field(default=False, description="Whether more results exist")


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error description")
    details: dict | None = Field(default=None, description="Optional field-level errors")


class ErrorResponse(BaseModel):
    error: ErrorDetail = Field(..., description="Error details")


class EmptyResponse(BaseModel):
    pass


class AccountIdQuery(BaseModel):
    account_id: int | None = Field(
        default=None, description="Mail account ID (defaults to primary account)"
    )


class BulkResponse(BaseModel):
    succeeded: list = Field(default_factory=list, description="Successfully processed items")
    failed: list = Field(default_factory=list, description="Failed items with error details")


class EventPublishBody(BaseModel):
    type: str = Field(..., description="Event type: 'ui_change' or 'ai_activity'")
    module: str | None = Field(default=None, description="Module name for ui_change events")
    action: str | None = Field(default=None, description="Action name for ui_change events")
    data: dict | None = Field(
        default=None, description="Payload for ui_change events (metadata + snippets only)"
    )
    tool: str | None = Field(default=None, description="Tool name for ai_activity events")
    summary: str | None = Field(
        default=None, description="Short activity summary (metadata + snippets only)"
    )
    account_id: int | None = Field(default=None, description="Account the activity touched")
    status: str | None = Field(default=None, description="Activity status: ok or error detail")
    duration_ms: int | None = Field(default=None, description="Activity duration in milliseconds")
    count: int | None = Field(
        default=None, description="Number of coalesced identical events (U27.3)"
    )
