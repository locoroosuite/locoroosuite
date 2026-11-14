from __future__ import annotations

from typing import Any

from app.shared.events import push_event


def push_ui_event(
    customer_id: int, module: str, action: str, data: dict[str, Any] | None = None
) -> None:
    push_event(
        customer_id,
        "ui_change",
        {
            "module": module,
            "action": action,
            **(data or {}),
        },
    )


def push_ai_activity(
    customer_id: int,
    tool: str,
    summary: str,
    *,
    account_id: int | None = None,
    status: str = "ok",
    duration_ms: int | None = None,
    count: int = 1,
    module: str | None = None,
) -> None:
    """U27.2: AI activity trace for the customer's own web UI stream.

    Payload is metadata + short snippets only — never bodies, attachments,
    tokens, or credentials (U27.9).
    """
    push_event(
        customer_id,
        "ai_activity",
        {
            "tool": tool,
            "summary": summary[:300],
            "module": module or (tool.split("_", 1)[0] if "_" in tool else None),
            "account_id": account_id,
            "status": status,
            "duration_ms": duration_ms,
            "count": count,
        },
    )
