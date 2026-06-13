import logging

from flask import Blueprint, session

from app.shared.db import db
from app.shared.models.core import Domain, CustomerAccount
from app.shared.keys import get_user_key
from app.modules.docs.services.cache import get_cache_path
from app.modules.docs.services.cache_db import open_cache as open_docs_cache


docs_bp = Blueprint("docs", __name__, template_folder="../templates")
logger = logging.getLogger(__name__)


def _get_account(account_id, user_id):
    return CustomerAccount.query.filter_by(
        id=account_id, customer_id=user_id, is_active=True
    ).first_or_404()


def _open_cache_for_account(account):
    key = get_user_key(session.get("user_id"))
    if not key:
        return None
    path = get_cache_path(account)
    return open_docs_cache(path, key)


def _user_key_hex():
    return get_user_key(session.get("user_id"))
