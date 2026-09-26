import base64
import logging
import re

from cryptography.fernet import Fernet
from flask import Blueprint, session
from flask_babel import _, ngettext

from app.modules.calendar.services.cache import get_cache_path
from app.modules.calendar.services.cache_db import open_cache as open_calendar_cache
from app.shared.db import db
from app.shared.keys import get_user_key
from app.shared.models.core import CustomerAccount, Domain

calendar_bp = Blueprint("calendar", __name__, template_folder="../templates")
logger = logging.getLogger(__name__)


def _decrypt_with_key(encrypted_value, derived_key_hex):
    key_bytes = bytes.fromhex(derived_key_hex)
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    f = Fernet(fernet_key)
    return f.decrypt(encrypted_value).decode()


def _get_account(account_id, user_id):
    return CustomerAccount.query.filter_by(
        id=account_id, customer_id=user_id, is_active=True
    ).first_or_404()


def _get_caldav_config(account):
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.caldav_host:
        return None
    return {
        "host": domain.caldav_host,
        "port": domain.caldav_port or 5232,
        "use_tls": domain.caldav_use_tls if domain.caldav_use_tls is not None else False,
    }


def _caldav_base_url(config):
    scheme = "https" if config["use_tls"] else "http"
    return f"{scheme}://{config['host']}:{config['port']}"


def _get_credentials(account):
    key = get_user_key(session.get("user_id"))
    if not key or not account.encrypted_secret:
        return None
    return _decrypt_with_key(account.encrypted_secret, key)


def _open_cache_for_account(account):
    key = get_user_key(session.get("user_id"))
    if not key:
        return None
    path = get_cache_path(account)
    if not path:
        return None
    return open_calendar_cache(path, key)


_DURATION_RE = re.compile(r"^-P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_negative_duration(text):
    """Parse a negative ISO 8601 duration (e.g. ``-PT15M``, ``-P1DT2H``).

    Returns ``(weeks, days, hours, minutes)`` ints, or ``None`` when the value
    is not a valid non-empty negative duration. ``-PT0M`` (at time of event)
    is valid and parses to all zeros.
    """
    if not isinstance(text, str):
        return None
    match = _DURATION_RE.match(text.strip())
    if match is None:
        return None
    if all(group is None for group in match.groups()):
        return None
    return tuple(int(group) if group else 0 for group in match.groups())


def _humanize_reminder(trigger_val, action):
    """Humanize one calendar reminder for display ("15 minutes before")."""
    parsed = parse_negative_duration(trigger_val)
    if parsed is None:
        when = trigger_val or ""
    else:
        weeks, days, hours, minutes = parsed
        if not (weeks or days or hours or minutes):
            when = _("At time of event")
        else:
            chunks = []
            if weeks:
                chunks.append(ngettext("%(num)d week", "%(num)d weeks", weeks))
            if days:
                chunks.append(ngettext("%(num)d day", "%(num)d days", days))
            if hours:
                chunks.append(ngettext("%(num)d hour", "%(num)d hours", hours))
            if minutes:
                chunks.append(ngettext("%(num)d minute", "%(num)d minutes", minutes))
            when = ", ".join(chunks) + " " + _("before")
    kind = _("Email") if (action or "DISPLAY").upper() == "EMAIL" else _("Notification")
    return {"when": when, "kind": kind}
