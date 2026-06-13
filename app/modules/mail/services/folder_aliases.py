FOLDER_ALIASES = {
    "sent": ["sent", "sent items", "sent messages", "inbox.sent"],
    "drafts": ["drafts", "draft messages", "inbox.drafts"],
    "trash": ["trash", "deleted", "deleted items", "deleted messages", "inbox.trash"],
    "junk": ["junk", "spam", "junk email", "junk e-mail", "bulk mail", "inbox.junk"],
    "archive": ["archive", "archives", "inbox.archive"],
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
