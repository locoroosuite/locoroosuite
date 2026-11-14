from __future__ import annotations

import base64
import contextvars
import dataclasses
import json
from typing import Any

from flask import Flask

from app.mcp.auth import (
    get_account_id,
    get_current_token,
    get_dek,
    require_scope,
    resolve_context,
)


@dataclasses.dataclass
class ToolCallContext:
    """U27: identity of the MCP tool call in flight — resilient_tool reads it
    after the call to emit ai_activity. ContextVar-scoped so concurrent tool
    calls never stomp on each other."""

    customer_id: int | None = None
    account_id: int | None = None
    summary_args: dict = dataclasses.field(default_factory=dict)


_current_tool_ctx: contextvars.ContextVar[ToolCallContext | None] = contextvars.ContextVar(
    "lr_mcp_tool_ctx", default=None
)


def set_tool_call_context(ctx: ToolCallContext) -> contextvars.Token:
    return _current_tool_ctx.set(ctx)


def reset_tool_call_context(token: contextvars.Token) -> None:
    _current_tool_ctx.reset(token)


def current_tool_call_context() -> ToolCallContext:
    return _current_tool_ctx.get() or ToolCallContext()


def ok(data: Any = None) -> str:
    """Flattened MCP tool result (U16.17): the resource payload is the
    top-level JSON — no `.data` envelope. Collections use ok_items()."""
    return json.dumps(data if data is not None else {}, default=str)


def ok_items(items: list) -> str:
    """Flattened collection result (U16.17): {"items": [...]} — the stable
    array key, so agent code never branches on JSON type."""
    return json.dumps({"items": list(items)}, default=str)


def ok_paginated(items: list, next_cursor: Any = None, has_more: bool = False) -> str:
    return json.dumps(
        {
            "items": list(items),
            "pagination": {"next_cursor": next_cursor, "has_more": has_more},
        },
        default=str,
    )


def err(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}})


def binary_response(data: bytes, mime_type: str, filename: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mime": mime_type,
        "encoding": "base64",
        "data": base64.b64encode(data).decode("ascii"),
    }
    if filename:
        result["filename"] = filename
    return result


def resolve_read(
    flask_app: Flask, module: str, account_id: int | None = None
) -> tuple[dict, int, str]:
    ctx = resolve_context(get_current_token(), flask_app)
    require_scope(ctx, module, "read")
    aid = get_account_id(ctx, flask_app, account_id)
    dek = get_dek(ctx, flask_app)
    _record_tool_context(ctx, aid)
    return ctx, aid, dek


def resolve_write(
    flask_app: Flask, module: str, account_id: int | None = None
) -> tuple[dict, int, str]:
    ctx = resolve_context(get_current_token(), flask_app)
    require_scope(ctx, module, "write")
    aid = get_account_id(ctx, flask_app, account_id)
    dek = get_dek(ctx, flask_app)
    _record_tool_context(ctx, aid)
    return ctx, aid, dek


def _record_tool_context(ctx: dict, aid: int) -> None:
    set_tool_call_context(ToolCallContext(customer_id=ctx.get("customer_id"), account_id=aid))
