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
    flagset = set(flags or [])
    if LOCKED_KEYWORD in flagset:
        return True
    return protect_starred_enabled(settings) and "\\Flagged" in flagset
