"""U27.4: cross-process event publishing.

The Python MCP server runs in its own process and relays ui_change /
ai_activity events here (app/mcp/events_bridge.py) so they reach the
customer's SSE stream on the Flask process that owns the event bus.
Events are pushed only to the token owner's own stream — a caller can
never publish to another customer (U27.9).
"""

from __future__ import annotations

from flask import g

from app.api.controllers.helpers import api_error, api_response, require_api_token
from app.api.openapi import create_api_blueprint
from app.api.schemas.common import EmptyResponse, ErrorResponse, EventPublishBody

bp = create_api_blueprint("events", "Cross-process event publishing (U27)")

_MAX_STR = 300


def _truncate(value, limit: int = _MAX_STR):
    if isinstance(value, str):
        return value[:limit]
    return value


@bp.post(
    "/events/publish",
    summary="Publish event",
    description="Publishes a ui_change or ai_activity event to the caller's own SSE stream. "
    "Used by the MCP server to relay events cross-process (U27.4). Requires any valid token.",
    responses={"200": EmptyResponse, "400": ErrorResponse, "401": ErrorResponse},
)
@require_api_token()
def api_publish_event(body: EventPublishBody):
    customer_id = g.api_context["customer_id"]
    event_type = (body.type or "").strip().lower()
    if event_type == "ui_change":
        if not body.module or not body.action:
            return api_error(
                "VALIDATION_ERROR", "'module' and 'action' are required for ui_change", 400
            )
        from app.shared.ui_events import push_ui_event

        data = {
            k: _truncate(v) if isinstance(v, str) else v
            for k, v in (body.data or {}).items()
            if k != "customer_id"
        }
        push_ui_event(customer_id, body.module, body.action, data)
        return api_response({"status": "published"})
    if event_type == "ai_activity":
        if not body.tool:
            return api_error("VALIDATION_ERROR", "'tool' is required for ai_activity", 400)
        from app.shared.ui_events import push_ai_activity

        push_ai_activity(
            customer_id,
            body.tool,
            body.summary or body.tool,
            account_id=body.account_id,
            status=body.status or "ok",
            duration_ms=body.duration_ms,
            count=body.count or 1,
        )
        return api_response({"status": "published"})
    return api_error("VALIDATION_ERROR", "'type' must be 'ui_change' or 'ai_activity'", 400)
