import os
import tempfile

os.environ.setdefault("APP_DATABASE_URI", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests")
# Keep push-keywrap artifacts out of the repo's data/ dir during tests (U24.29).
os.environ.setdefault(
    "PUSH_KEYWRAP_KEY_PATH",
    os.path.join(tempfile.mkdtemp(prefix="lr-push-test-"), "push_keywrap.key"),
)

from unittest.mock import MagicMock, patch

import pytest

from app.shared.db import db as _db
from app.shared.keys import clear_user_key, set_user_key
from app.shared.models.core import CustomerAccount, Domain, User

_test_app = None


@pytest.fixture(scope="session")
def app():
    global _test_app
    if _test_app is not None:
        yield _test_app
        return

    try:
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()

            from app import create_app

            app = create_app()
            app.config["TESTING"] = True
            app.config["WTF_CSRF_ENABLED"] = False
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"

            with app.app_context():
                _db.create_all()

            _test_app = app
            yield app
    finally:
        if _test_app is not None:
            with _test_app.app_context():
                _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_db(app):
    with app.app_context():
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()
    yield


@pytest.fixture()
def authed_client(app, client, _clean_db):
    user_id = None
    account_id = None
    with app.app_context():
        user = User()
        user.email = "test@example.com"
        user.role = "customer"
        user.is_active = True
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain()
        domain.name = "example.com"
        domain.is_active = True
        domain.status = "active"
        domain.imap_host = "imap.example.com"
        domain.imap_port = 993
        domain.imap_tls = True
        domain.smtp_host = "smtp.example.com"
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        _db.session.add(domain)
        _db.session.flush()

        account = CustomerAccount()
        account.customer_id = user.id
        account.domain_id = domain.id
        account.email_address = "test@example.com"
        account.auth_type = "password"
        account.username = "test@example.com"
        account.cache_db_path = ""
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    with client.session_transaction() as sess:
        sess["role"] = "customer"
        sess["user_id"] = user_id
        sess["active_account_id"] = account_id

    yield client, user_id, account_id

    clear_user_key(user_id)
