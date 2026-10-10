FOLDER_ALIASES = {
    "sent": ["sent", "sent items", "sent messages", "inbox.sent"],
    "drafts": ["drafts", "draft messages", "inbox.drafts"],
    "trash": ["trash", "deleted", "deleted items", "deleted messages", "inbox.trash"],
    "junk": ["junk", "spam", "junk email", "junk e-mail", "bulk mail", "inbox.junk"],
    "archive": ["archive", "archives", "inbox.archive"],
}

# U4.15b: canonical English display labels for system folders. Display-only;
# raw IMAP names remain the identifier in URLs, operations, REST API, and MCP.
CANONICAL_DISPLAY_LABELS = {
    "inbox": "INBOX",
    "sent": "Sent",
    "drafts": "Drafts",
    "trash": "Trash",
    "junk": "Spam",
    "archive": "Archive",
}

_ALIAS_TO_CANONICAL = {}
for _canonical, _aliases in FOLDER_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias] = _canonical


def resolve_folder_name(available_folders, requested_name):
    available_lower = {name.lower(): name for name in available_folders}
    key = requested_name.lower()
    if key in available_lower:
        return available_lower[key]
    canonical = _ALIAS_TO_CANONICAL.get(key, key)
    if canonical != key and canonical in available_lower:
        return available_lower[canonical]
    aliases = FOLDER_ALIASES.get(canonical, [])
    for alias in aliases:
        if alias in available_lower:
            return available_lower[alias]
    return requested_name


def canonical_folder_key(folder_name):
    return _ALIAS_TO_CANONICAL.get(folder_name.lower(), folder_name.lower())


def folder_display_names(folders):
    """Map raw IMAP folder names to canonical display labels (U4.15b).

    System folders show their canonical English label ("Spam" for any junk
    alias). When several folders map to the same canonical key (e.g. both
    "Junk" and "Spam" exist), the raw name is suffixed for disambiguation.
    Non-system folders pass through unchanged.
    """
    key_counts: dict[str, int] = {}
    for folder in folders:
        key = canonical_folder_key(folder)
        key_counts[key] = key_counts.get(key, 0) + 1
    display = {}
    for folder in folders:
        key = canonical_folder_key(folder)
        label = CANONICAL_DISPLAY_LABELS.get(key)
        if label is None:
            continue
        display[folder] = label if key_counts[key] == 1 else f"{label} ({folder})"
    return display
