import os
import tempfile

os.environ.setdefault("APP_DATABASE_URI", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests")
# Keep push-keywrap artifacts out of the repo's data/ dir during tests (U24.29).
os.environ.setdefault(
    "PUSH_KEYWRAP_KEY_PATH",
    os.path.join(tempfile.mkdtemp(prefix="lr-push-test-"), "push_keywrap.key"),
)

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.shared.db import db as _db
from app.shared.keys import clear_user_key, set_user_key
from app.shared.models.core import CustomerAccount, Domain, User

# Per-account cache DBs must never land in the repo's data/caches dir:
# fixtures that rely on build_cache_path's default would share files across
# sessions with different DEKs (CacheKeyMismatchError flakiness in combined
# runs) and leave artifacts in the tree. Redirect every module's CACHE_DIR
# to a per-session temp dir instead.
_TEST_CACHE_DIR = Path(tempfile.mkdtemp(prefix="lr-caches-test-"))


def _redirect_cache_dirs() -> None:
    from app.modules.calendar.services import cache as _cal_cache
    from app.modules.chat.services import cache as _chat_cache
    from app.modules.contacts.services import cache as _contacts_cache
    from app.modules.docs.services import cache as _docs_cache
    from app.modules.mail.services import cache as _mail_cache

    for module in (_cal_cache, _chat_cache, _contacts_cache, _docs_cache, _mail_cache):
        module.CACHE_DIR = _TEST_CACHE_DIR  # pyright: ignore[reportAttributeAccessIssue]


_test_app = None


@pytest.fixture(scope="session")
def app():
    global _test_app
    if _test_app is not None:
        yield _test_app
        return

    try:
        with (
            # No background threads in unit tests: the workers share the
            # in-memory SQLite connection with test fixtures and race the
            # per-test _clean_db wipe (ghost rows reappear, admin rows
            # momentarily vanish -> random /admin/setup redirects and
            # UNIQUE-constraint errors, ~1 run in N, different test each
            # time). WorkerManager was already mocked; the calendar/chat
            # workers were missed and ran for real.
            patch("app.workers.manager.WorkerManager") as MockWM,
            patch("app.workers.calendar_reminders.CalendarReminderWorker") as MockCal,
            patch("app.workers.chat_push.ChatPushWorker") as MockChat,
        ):
            MockWM.return_value = MagicMock()
            MockCal.return_value = MagicMock()
            MockChat.return_value = MagicMock()

            from app import create_app

            _redirect_cache_dirs()
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
