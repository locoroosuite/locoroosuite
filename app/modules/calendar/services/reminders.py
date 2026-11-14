"""Reminder (VALARM) normalization shared by the web UI, REST API, and MCP
tools (U12.30, U15.56, U15.57).

The REST/MCP contract speaks ``{type, trigger_minutes}``; iCalendar speaks
``ACTION`` + ``TRIGGER`` durations. Both directions are normalized here so
no surface can emit an invalid VALARM (issue #10) or drop reminders on
reads (issue #11).
"""

from __future__ import annotations

import re
from typing import Any

REMINDER_ACTIONS = ("DISPLAY", "EMAIL", "PUSH")

_DURATION_RE = re.compile(r"^-P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")

MAX_TRIGGER_MINUTES = 60 * 24 * 7  # one week before — anything larger is a typo


def _parse_negative_duration_minutes(text: str) -> int | None:
    """Parse a negative ISO 8601 duration into total minutes, or None."""
    if not isinstance(text, str):
        return None
    match = _DURATION_RE.match(text.strip())
    if match is None:
        return None
    if all(group is None for group in match.groups()):
        return None
    weeks, days, hours, minutes = (int(g) if g else 0 for g in match.groups())
    return weeks * 7 * 24 * 60 + days * 24 * 60 + hours * 60 + minutes


def reminders_to_alarms(
    reminders: Any, summary: str = ""
) -> tuple[list[dict[str, Any]], str | None]:
    """Validate a ``[{type, trigger_minutes}]`` payload into VALARM dicts.

    Returns ``(alarms, error)`` — exactly one is empty/None on success.
    Types are normalized (``display`` → ``DISPLAY``), unknown types and
    non-integer triggers are rejected before CalDAV is touched (U15.57).
    """
    alarms: list[dict[str, Any]] = []
    error: str | None = None
    if reminders in (None, ""):
        return alarms, None
    if isinstance(reminders, str):
        import json

        try:
            reminders = json.loads(reminders)
        except ValueError:
            return alarms, "Reminders must be an array of {type, trigger_minutes}."
    if not isinstance(reminders, list):
        return alarms, "Reminders must be an array of {type, trigger_minutes}."
    seen: set[tuple[str, int]] = set()
    for item in reminders:
        if not isinstance(item, dict):
            return alarms, "Each reminder must be an object with type and trigger_minutes."
        raw_type = str(item.get("type") or item.get("action") or "DISPLAY").strip().upper()
        if raw_type not in REMINDER_ACTIONS:
            return alarms, (
                f"Invalid reminder type {raw_type!r}. "
                f"Supported types: {', '.join(REMINDER_ACTIONS)}."
            )
        raw_trigger = item.get("trigger_minutes", item.get("trigger"))
        if isinstance(raw_trigger, bool) or not isinstance(raw_trigger, (int, float, str)):
            return alarms, "trigger_minutes must be an integer number of minutes."
        try:
            minutes = int(str(raw_trigger).strip())
        except ValueError:
            return alarms, "trigger_minutes must be an integer number of minutes."
        if minutes < 0 or minutes > MAX_TRIGGER_MINUTES:
            return alarms, (
                f"trigger_minutes must be between 0 and {MAX_TRIGGER_MINUTES} (one week)."
            )
        if (raw_type, minutes) in seen:
            return alarms, f"Duplicate reminder ({raw_type}, {minutes} minutes)."
        seen.add((raw_type, minutes))
        alarms.append(
            {
                "action": raw_type,
                "trigger": _minutes_to_trigger(minutes),
                "description": (summary or "").strip(),
            }
        )
    return alarms, error


def _minutes_to_trigger(minutes: int) -> str:
    if minutes == 0:
        return "-PT0M"
    weeks, rem = divmod(minutes, 7 * 24 * 60)
    days, rem = divmod(rem, 24 * 60)
    hours, mins = divmod(rem, 60)
    out = "-P"
    if weeks:
        out += f"{weeks}W"
    if days:
        out += f"{days}D"
    if hours or mins:
        out += "T"
        if hours:
            out += f"{hours}H"
        if mins:
            out += f"{mins}M"
    return out


def alarms_to_reminders(alarms: Any) -> list[dict]:
    """Convert parsed VALARMs back to the API contract ``{type, trigger_minutes}``.

    Unparseable triggers keep the raw value in ``trigger`` for debugging.
    """
    if not alarms:
        return []
    out: list[dict] = []
    for alarm in alarms:
        if not isinstance(alarm, dict):
            continue
        action = str(alarm.get("action") or "DISPLAY").upper()
        if action not in REMINDER_ACTIONS:
            action = "DISPLAY"
        trigger = str(alarm.get("trigger") or "")
        minutes = _parse_negative_duration_minutes(trigger)
        item: dict[str, Any] = {"type": action}
        if minutes is None:
            item["trigger"] = trigger
        else:
            item["trigger_minutes"] = minutes
        out.append(item)
    return out
