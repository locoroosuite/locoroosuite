import os
import tempfile

import pytest

from app.shared.db import db as _db
from app.shared.models.core import User, Domain, CustomerAccount
from app.shared.keys import set_user_key, clear_user_key


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.api.controllers import helpers as _helpers

    _helpers._rate_limit_store.clear()
    yield


@pytest.fixture()
def api_customer(app, client, _clean_db):
    user_id = None
    account_id = None
    with app.app_context():
        user = User(email="api@example.com", role="customer", is_active=True)
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain(
            name="example.com",
            is_active=True,
            status="active",
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        _db.session.add(domain)
        _db.session.flush()

        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address="api@example.com",
            auth_type="password",
            username="api@example.com",
            cache_db_path="",
            api_enabled=True,
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    yield client, user_id, account_id

    clear_user_key(user_id)


def setup_cache_db(app, account_id, cache_path_fn=None):
    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        if cache_path_fn:
            cache_path = cache_path_fn(account)
        else:
            f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            cache_path = f.name
            f.close()
            account.cache_db_path = cache_path
            _db.session.commit()
    if cache_path_fn and os.path.exists(cache_path):
        os.unlink(cache_path)
    return cache_path


def cleanup_cache_db(cache_path):
    try:
        os.unlink(cache_path)
    except OSError:
        pass


def create_api_token(app, customer_id, dek_hex="a" * 64, name="test-token", scopes=None):
    from app.api.token_service import create_api_token as _create

    if scopes is None:
        scopes = [
            "mail:read",
            "mail:write",
            "contacts:read",
            "contacts:write",
            "calendar:read",
            "calendar:write",
            "docs:read",
            "docs:write",
        ]
    return _create(customer_id, dek_hex, name, scopes)


def auth_header(token_value):
    return {"Authorization": f"Bearer {token_value}"}
