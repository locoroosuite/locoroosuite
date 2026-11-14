from __future__ import annotations

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class BulkMessageIdItem(BaseModel):
    message_id: int


class BulkFlagItem(BaseModel):
    message_id: int
    flags: dict[str, bool]


class BulkContactIdItem(BaseModel):
    contact_id: int


class EventAttendee(BaseModel):
    email: str = Field(description="Attendee email address")
    cn: str | None = Field(default=None, description="Display name (defaults to email)")
    role: Literal["REQ-PARTICIPANT", "OPT-PARTICIPANT"] | None = Field(
        default=None, description="Participation role"
    )
    partstat: str | None = Field(
        default=None,
        description="Participation status (NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE)",
    )
    rsvp: str | None = Field(default=None, description="Whether RSVP is expected (TRUE or FALSE)")


class EventReminder(BaseModel):
    type: str | None = Field(
        default=None,
        description="Reminder type: DISPLAY, EMAIL, or PUSH (case-insensitive, default DISPLAY)",
    )
    trigger_minutes: int | str | None = Field(
        default=None,
        description="Minutes before the event start (e.g. 15 for 15 minutes before, 0 for at start)",
    )


def validate_bulk_items[T: BaseModel](
    items: list[dict[str, Any]], model_class: type[T], max_items: int = 100
) -> tuple[list[T], list[dict[str, Any]]]:
    validated: list[T] = []
    errors: list[dict[str, Any]] = []
    if not items:
        return validated, [
            {"index": -1, "error": {"code": "VALIDATION_ERROR", "message": "items array is empty"}}
        ]
    if len(items) > max_items:
        return validated, [
            {
                "index": -1,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": f"Too many items (max {max_items})",
                },
            }
        ]
    for i, raw in enumerate(items):
        try:
            validated.append(model_class.model_validate(raw))
        except Exception as exc:
            errors.append({"index": i, "error": {"code": "VALIDATION_ERROR", "message": str(exc)}})
    return validated, errors


def ensure_typed[T: BaseModel](items: list, model_class: type[T]) -> list[T]:
    return [
        item if isinstance(item, model_class) else model_class.model_validate(item)
        for item in items
    ]
