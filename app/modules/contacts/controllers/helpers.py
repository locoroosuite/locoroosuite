import logging
import base64

from flask import Blueprint, session

from cryptography.fernet import Fernet

from app.shared.db import db
from app.shared.models.core import Domain, CustomerAccount
from app.shared.keys import get_user_key
from app.modules.contacts.services.cache import get_cache_path
from app.modules.contacts.services.cache_db import open_cache as open_contacts_cache


contacts_bp = Blueprint("contacts", __name__, template_folder="../templates")
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


def _get_carddav_config(account):
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.carddav_host:
        return None
    return {
        "host": domain.carddav_host,
        "port": domain.carddav_port or 5232,
        "use_tls": domain.carddav_use_tls if domain.carddav_use_tls is not None else False,
    }


def _carddav_base_url(config):
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
    return open_contacts_cache(path, key)
