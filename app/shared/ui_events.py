from __future__ import annotations

from typing import Any

from app.shared.events import push_event


def push_ui_event(customer_id: int, module: str, action: str, data: dict[str, Any] | None = None) -> None:
    push_event(customer_id, "ui_change", {
        "module": module,
        "action": action,
        **(data or {}),
    })
