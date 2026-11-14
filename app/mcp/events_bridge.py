"""U27.4: MCP → web UI event bridge.

The Python MCP server runs in its own process from the Flask app that owns
the SSE event bus and its subscribers (U17.21 single-port deployment).
Events pushed in-process inside the MCP server never reach a browser, so
this bridge relays them over HTTP to the Flask backend's
``POST /api/v1/events/publish`` endpoint using the caller's own token —
the event still lands only on the acting customer's own stream.

The relay is the single delivery path (no in-process push: the MCP
process has no SSE consumers, and double-pushing would duplicate events
in single-process embeddings — U27.4 "no double-emission"). Everything is
fire-and-forget: a bridging failure must never fail or delay a tool call
(logged, then dropped).

Modes (LR_MCP_EVENT_BRIDGE): "1"/"relay" (default) — HTTP relay only;
"local" — in-process push only (single-process embeddings/tests);
"0"/"false"/"off" — disabled (no-op).
"""

import asyncio
import logging
import os
from typing import Any

import httpx

from app.shared.ui_events import push_ai_activity, push_ui_event

logger = logging.getLogger(__name__)

_BACKEND_URL = os.environ.get("FLASK_BACKEND_URL", "http://localhost:5001")
_PUBLISH_TIMEOUT = httpx.Timeout(3.0, connect=2.0)

_mode = os.environ.get("LR_MCP_EVENT_BRIDGE", "1").strip().lower()
if _mode in ("0", "false", "off"):
    _BRIDGE_MODE = "off"
elif _mode == "local":
    _BRIDGE_MODE = "local"
else:
    _BRIDGE_MODE = "relay"

_relay_tasks: set[asyncio.Task] = set()


def _relay_async(payload: dict[str, Any]) -> None:
    """Schedule the HTTP relay on the running event loop, if any."""

    async def _post() -> None:
        try:
            from app.mcp.auth import get_current_token

            token = get_current_token()
            if not token:
                return
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=_PUBLISH_TIMEOUT) as client:
                await client.post(
                    f"{_BACKEND_URL}/api/v1/events/publish", json=payload, headers=headers
                )
        except Exception:
            logger.warning(
                "event bridge relay failed payload_type=%s backend=%s",
                payload.get("type"),
                _BACKEND_URL,
                exc_info=True,
            )

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_post())
        _relay_tasks.add(task)
        task.add_done_callback(_relay_tasks.discard)
    except RuntimeError:
        # No running loop (sync context): a tool call cannot be in flight
        # in this state; drop with a breadcrumb.
        logger.warning("event bridge skipped: no running event loop")


def _publish(local_push, relay_payload: dict[str, Any]) -> None:
    if _BRIDGE_MODE == "off":
        return
    if _BRIDGE_MODE == "local":
        try:
            local_push()
        except Exception:
            logger.warning("in-process event push failed", exc_info=True)
        return
    _relay_async(relay_payload)


def bridge_ui_event(
    customer_id: int, module: str, action: str, data: dict[str, Any] | None = None
) -> None:
    """MCP-side replacement for push_ui_event (relayed cross-process)."""
    _publish(
        lambda: push_ui_event(customer_id, module, action, data),
        {
            "type": "ui_change",
            "module": module,
            "action": action,
            "data": {**(data or {}), "customer_id": customer_id},
        },
    )


def bridge_ai_activity(
    customer_id: int,
    tool: str,
    summary: str,
    *,
    account_id: int | None = None,
    status: str = "ok",
    duration_ms: int | None = None,
    count: int = 1,
) -> None:
    """MCP-side ai_activity emission (U27.2, relayed cross-process)."""
    _publish(
        lambda: push_ai_activity(
            customer_id,
            tool,
            summary,
            account_id=account_id,
            status=status,
            duration_ms=duration_ms,
            count=count,
        ),
        {
            "type": "ai_activity",
            "tool": tool,
            "summary": summary,
            "account_id": account_id,
            "status": status,
            "duration_ms": duration_ms,
            "count": count,
            "customer_id": customer_id,
        },
    )
