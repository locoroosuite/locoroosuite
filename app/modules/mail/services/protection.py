from __future__ import annotations

import json
from typing import Any, Iterable

LOCKED_KEYWORD = "$Locked"

SYSTEM_FOLDERS = {
    "inbox": True,
    "sent": True,
    "drafts": True,
    "trash": True,
    "junk": True,
    "spam": True,
    "bookings": True,
}


def is_system_folder(folder: str | None) -> bool:
    return bool(folder) and folder.strip().lower() in SYSTEM_FOLDERS


def load_protected_folders(settings: Any) -> list[str]:
    if not settings or not getattr(settings, "protected_folders", None):
        return []
    try:
        value = json.loads(settings.protected_folders)
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def folder_is_protected(settings: Any, folder: str | None) -> bool:
    if is_system_folder(folder):
        return True
    target = (folder or "").lower()
    return any(p.lower() == target for p in load_protected_folders(settings))


def set_folder_protected(settings: Any, folder: str, protected: bool) -> None:
    current = load_protected_folders(settings)
    lower = {p.lower() for p in current}
    if protected:
        if folder.lower() not in lower:
            current.append(folder)
    else:
        current = [p for p in current if p.lower() != folder.lower()]
    settings.protected_folders = json.dumps(current)


def load_locked_keyword_prefs(settings: Any) -> dict[str, bool]:
    if not settings or not getattr(settings, "locked_keyword_prefs", None):
        return {}
    try:
        value = json.loads(settings.locked_keyword_prefs)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def locked_keyword_enabled(settings: Any, account_id: int | str) -> bool:
    prefs = load_locked_keyword_prefs(settings)
    return bool(prefs.get(str(account_id), True))


def set_locked_keyword_enabled(settings: Any, account_id: int | str, enabled: bool) -> None:
    prefs = load_locked_keyword_prefs(settings)
    prefs[str(account_id)] = bool(enabled)
    settings.locked_keyword_prefs = json.dumps(prefs)


def protect_starred_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "protect_starred", True))


def message_is_protected(flags: Iterable[str] | None, settings: Any) -> bool:
    """Return True if a message must be refused for delete/move-to-Trash.

    A message is protected when it carries the explicit ``$Locked`` keyword, or
    when "protect starred" is enabled and the message is flagged (``\\Flagged``).
    """
    return protection_reason(flags, settings) is not None


def protection_reason(flags: Iterable[str] | None, settings: Any) -> str | None:
    """Return the active protection reason, or ``None`` if not protected.

    The result is a stable token callers can branch on:

    * ``"locked"``        - the message carries ``$Locked``
    * ``"starred"``       - the message is ``\\Flagged`` and protect-starred is on
    * ``"starred+locked"``- both conditions hold
    * ``None``            - the message is not protected

    ``message_is_protected`` is a thin predicate over this helper so the two
    answers can never drift apart.
    """
    flagset = set(flags or [])
    locked = LOCKED_KEYWORD in flagset
    starred = protect_starred_enabled(settings) and "\\Flagged" in flagset
    if locked and starred:
        return "starred+locked"
    if locked:
        return "locked"
    if starred:
        return "starred"
    return None


def protected_delete_message(reason: str) -> str:
    """Build a specific, actionable user-facing message (HLD U5.15g).

    The wording points the user at the ⋯ menu control that resolves the
    protection, and deliberately omits any "retry" guidance since there is
    nothing to retry.
    """
    if reason == "locked":
        return (
            "This message is locked. Click Unlock in the \u22ef menu to allow deletion."
        )
    if reason == "starred":
        return (
            "This message is starred. Click Unstar in the \u22ef menu to allow deletion."
        )
    # starred+locked (or any unexpected value) - resolve both.
    return (
        "This message is starred and locked. Unstar and Unlock it in the \u22ef menu "
        "to allow deletion."
    )


def protected_badge_label(reason: str) -> str:
    """Short label for the Protected badge tooltip / aria-label (HLD U5.15h)."""
    if reason == "locked":
        return "Protected: locked"
    if reason == "starred":
        return "Protected: starred"
    return "Protected: starred and locked"
