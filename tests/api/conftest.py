import contextlib
import os
import tempfile

import pytest

from app.shared.db import db as _db
from app.shared.keys import clear_user_key, set_user_key
from app.shared.models.core import CustomerAccount, Domain, User


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
        user = User(email="api@example.com", role="customer", is_active=True)  # pyright: ignore[reportCallIssue]
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain(
            name="example.com",  # pyright: ignore[reportCallIssue]
            is_active=True,  # pyright: ignore[reportCallIssue]
            status="active",  # pyright: ignore[reportCallIssue]
            imap_host="imap.example.com",  # pyright: ignore[reportCallIssue]
            imap_port=993,  # pyright: ignore[reportCallIssue]
            imap_tls=True,  # pyright: ignore[reportCallIssue]
            smtp_host="smtp.example.com",  # pyright: ignore[reportCallIssue]
            smtp_port=587,  # pyright: ignore[reportCallIssue]
            smtp_tls_mode="starttls",  # pyright: ignore[reportCallIssue]
        )
        _db.session.add(domain)
        _db.session.flush()

        account = CustomerAccount(
            customer_id=user.id,  # pyright: ignore[reportCallIssue]
            domain_id=domain.id,  # pyright: ignore[reportCallIssue]
            email_address="api@example.com",  # pyright: ignore[reportCallIssue]
            auth_type="password",  # pyright: ignore[reportCallIssue]
            username="api@example.com",  # pyright: ignore[reportCallIssue]
            # Unique temp cache per test: an empty path would fall back to
            # build_cache_path (customer_N_account_M.db), and those row ids
            # are reused after every _clean_db wipe — oauth-suite leftovers
            # then collide with later suites' DEKs ("file is not a database").
            cache_db_path=tempfile.mkstemp(suffix=".db")[1],  # pyright: ignore[reportCallIssue]
            api_enabled=True,  # pyright: ignore[reportCallIssue]
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    yield client, user_id, account_id

    with contextlib.suppress(OSError):
        os.unlink(_account_cache_path(app, account_id))
    clear_user_key(user_id)


def _account_cache_path(app, account_id):
    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        return account.cache_db_path if account else ""


def setup_cache_db(app, account_id, cache_path_fn=None):
    with app.app_context():
        account = _db.session.get(CustomerAccount, account_id)
        if cache_path_fn:
            cache_path = cache_path_fn(account)
        else:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
                cache_path = tmp_file.name
            account.cache_db_path = cache_path
            _db.session.commit()
    if cache_path_fn and os.path.exists(cache_path):
        os.unlink(cache_path)
    return cache_path


def cleanup_cache_db(cache_path):
    with contextlib.suppress(OSError):
        os.unlink(cache_path)


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
