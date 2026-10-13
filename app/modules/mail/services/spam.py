import imaplib
import json
import logging

from app.modules.mail.services.folder_aliases import canonical_folder_key
from app.modules.mail.services.imap_client import (
    list_folders_strict,
    move_message,
    set_flag,
)

_logger = logging.getLogger(__name__)

# Values stored per account (string key) in the spam_action_prefs JSON:
#   missing / True   -> enabled (default, U9.5)
#   "user_off"       -> the customer turned it off in Settings; sticky,
#                       never auto-re-enabled
#   "auto_off"       -> auto-disabled after a *confirmed* server capability
#                       failure (U5.13); re-probed on the Settings page and
#                       re-enabled once the server lists a junk-canonical
#                       folder again
# Legacy boolean False values are migrated to "auto_off" (migration 0022):
# the pre-fix auto-disable path fired on transient errors, so a stored False
# is presumed accidental rather than deliberate.
_PREF_USER_OFF = "user_off"
_PREF_AUTO_OFF = "auto_off"
_DISABLED_VALUES = (False, _PREF_USER_OFF, _PREF_AUTO_OFF)


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
    return prefs.get(str(account_id), True) not in _DISABLED_VALUES


def spam_action_auto_disabled(settings, account_id):
    prefs = load_spam_action_prefs(settings)
    return prefs.get(str(account_id)) == _PREF_AUTO_OFF


def set_spam_action_enabled(settings, account_id, enabled):
    """Write the user-facing toggle (Settings -> Protection)."""
    prefs = load_spam_action_prefs(settings)
    prefs[str(account_id)] = True if enabled else _PREF_USER_OFF
    settings.spam_action_prefs = json.dumps(prefs)


def set_spam_auto_disabled(settings, account_id):
    """Record a confirmed capability failure (U5.13). Recoverable."""
    prefs = load_spam_action_prefs(settings)
    prefs[str(account_id)] = _PREF_AUTO_OFF
    settings.spam_action_prefs = json.dumps(prefs)


def maybe_reenable_auto_disabled(settings, account_id, client):
    """Re-enable an auto-disabled account when the server is healthy again.

    Probes the server for a junk-canonical folder (U5.12: a refused LIST is
    transient and leaves the pref untouched). Returns True when the pref
    changed; the caller owns the session commit.
    """
    if not spam_action_auto_disabled(settings, account_id):
        return False
    try:
        if spam_destination(client) is None:
            return False
    except SpamTransientError:
        return False
    prefs = load_spam_action_prefs(settings)
    prefs.pop(str(account_id), None)
    settings.spam_action_prefs = json.dumps(prefs)
    _logger.info(
        "spam action re-enabled automatically account_id=%s: junk folder is listable again",
        account_id,
    )
    return True


class SpamFolderMissingError(Exception):
    """A successful LIST contains no Junk/Spam (or aliased) folder."""


class SpamFlagUnsupportedError(Exception):
    """The server rejected the \\Junk flag for a message that still exists."""


class SpamTransientError(Exception):
    """A transient failure: LIST refused, message vanished mid-action, or a
    connection problem. Never disables the per-account setting (U5.13)."""


def spam_destination(client):
    try:
        folders = list_folders_strict(client)
    except imaplib.IMAP4.error as exc:
        raise SpamTransientError(f"folder list failed: {exc}") from exc
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


def _uid_exists(client, uid):
    """Best-effort check telling a vanished message from a rejected flag."""
    try:
        status, data = client.uid("SEARCH", None, f"UID {uid}")
        return status == "OK" and bool(data and data[0])
    except imaplib.IMAP4.error:
        # Cannot tell — assume the message is gone rather than auto-disabling
        # on doubt (U5.13: transient failures never change the setting).
        return False


def report_spam(client, uid):
    """Set \\Junk on the selected folder's message and move it to the junk folder.

    Returns the destination folder name. Raises SpamFolderMissingError when a
    successful LIST shows no junk-canonical folder, SpamFlagUnsupportedError
    when the server rejects the \\Junk flag for a message verified to still
    exist, and SpamTransientError for transient failures (refused LIST,
    vanished message, connection problems).
    """
    destination = spam_destination(client)
    if not destination:
        raise SpamFolderMissingError()
    try:
        set_flag(client, uid, "\\Junk", add=True)
    except imaplib.IMAP4.error as exc:
        if _uid_exists(client, uid):
            raise SpamFlagUnsupportedError() from exc
        _logger.warning(
            "spam report hit a vanished or unreachable message uid=%s destination=%s: %s",
            uid,
            destination,
            exc,
        )
        raise SpamTransientError(f"junk flag failed: {exc}") from exc
    try:
        move_message(client, uid, destination)
        client.expunge()
    except imaplib.IMAP4.error as exc:
        _logger.warning("spam report move failed uid=%s destination=%s: %s", uid, destination, exc)
        raise SpamTransientError(f"move to junk failed: {exc}") from exc
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
