from __future__ import annotations

import base64
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


def ok(data: Any = None) -> str:
    body: dict[str, Any] = {}
    if data is not None:
        body["data"] = data
    return json.dumps(body)


def ok_paginated(items: list, next_cursor: Any = None, has_more: bool = False) -> str:
    return json.dumps({
        "data": items,
        "pagination": {"next_cursor": next_cursor, "has_more": has_more},
    })


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


def resolve_read(flask_app: Flask, module: str, account_id: int | None = None) -> tuple[dict, int, str]:
    ctx = resolve_context(get_current_token(), flask_app)
    require_scope(ctx, module, "read")
    aid = get_account_id(ctx, flask_app, account_id)
    dek = get_dek(ctx, flask_app)
    return ctx, aid, dek


def resolve_write(flask_app: Flask, module: str, account_id: int | None = None) -> tuple[dict, int, str]:
    ctx = resolve_context(get_current_token(), flask_app)
    require_scope(ctx, module, "write")
    aid = get_account_id(ctx, flask_app, account_id)
    dek = get_dek(ctx, flask_app)
    return ctx, aid, dek
