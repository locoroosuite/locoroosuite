import imaplib
import json
import logging

from app.modules.mail.services.folder_aliases import canonical_folder_key
from app.modules.mail.services.imap_client import list_folders, move_message, set_flag

_logger = logging.getLogger(__name__)


def load_spam_action_prefs(settings):
    if not settings or not settings.spam_action_prefs:
        return {}
    try:
        prefs = json.loads(settings.spam_action_prefs)
        return prefs if isinstance(prefs, dict) else {}
    except (TypeError, ValueError):
        _logger.warning("invalid spam_action_prefs JSON for customer settings row")
        return {}


def ensure_settings(customer_id):
    """Return the customer's settings row, creating it if missing."""
    from app.shared.db import db
    from app.shared.models.core import CustomerSettings

    settings = CustomerSettings.query.filter_by(customer_id=customer_id).first()
    if not settings:
        settings = CustomerSettings()
        settings.customer_id = customer_id
        db.session.add(settings)
        db.session.commit()
    return settings


def spam_action_enabled(settings, account_id):
    prefs = load_spam_action_prefs(settings)
    return prefs.get(str(account_id), True)


def set_spam_action_enabled(settings, account_id, enabled):
    prefs = load_spam_action_prefs(settings)
    prefs[str(account_id)] = bool(enabled)
    settings.spam_action_prefs = json.dumps(prefs)


class SpamFolderMissingError(Exception):
    """The IMAP server has no Junk/Spam (or aliased) folder."""


class SpamFlagUnsupportedError(Exception):
    """The IMAP server rejected the \\Junk flag."""


def spam_destination(client):
    folders = list_folders(client)
    by_lower = {name.lower(): name for name in folders}
    if "junk" in by_lower:
        return by_lower["junk"]
    if "spam" in by_lower:
        return by_lower["spam"]
    for name in folders:
        if canonical_folder_key(name) == "junk":
            return name
    return None


def is_junk_folder(folder_name):
    return canonical_folder_key(folder_name or "") == "junk"


def report_spam(client, uid):
    """Set \\Junk on the selected folder's message and move it to the junk folder.

    Returns the destination folder name. Raises SpamFolderMissingError when no
    junk-canonical folder exists and SpamFlagUnsupportedError when the server
    rejects the \\Junk flag.
    """
    destination = spam_destination(client)
    if not destination:
        raise SpamFolderMissingError()
    try:
        set_flag(client, uid, "\\Junk", add=True)
    except imaplib.IMAP4.error:
        raise SpamFlagUnsupportedError() from None
    move_message(client, uid, destination)
    client.expunge()
    return destination


def clear_junk_flag(client, uid, folder):
    try:
        set_flag(client, uid, "\\Junk", add=False)
    except imaplib.IMAP4.error:
        _logger.warning("junk flag clear failed folder=%s uid=%s", folder, uid, exc_info=True)


def not_spam(client, uid, folder):
    """Clear \\Junk and move the message to INBOX when it is in a junk folder.

    Returns "INBOX" when the message was moved, None when only the flag was
    cleared (message already outside a junk-canonical folder). Flag-clear
    failures are logged and never abort the move (U5.13b).
    """
    destination = "INBOX" if is_junk_folder(folder) else None
    clear_junk_flag(client, uid, folder)
    if destination:
        move_message(client, uid, destination)
        client.expunge()
    return destination
