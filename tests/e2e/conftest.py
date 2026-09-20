import contextlib
import os

import pytest

from tests.e2e.services import (
    APP_URL,
    E2E_DEFAULT_PASSWORD,
    E2E_TEST_USERS,
    MAIL_API_URL,
    admin_session,
    check_services,
    cleanup_e2e_contacts,
    cleanup_e2e_users,
    get_account_id,
    login_session,
    setup_e2e_users,
)


def _is_e2e_enabled():
    return os.environ.get("E2E_ENABLED", "").lower() in ("1", "true", "yes") or check_services()


skip_if_no_services = pytest.mark.skipif(
    not _is_e2e_enabled(),
    reason="E2E services not running. Start with: make dev-up",
)


@pytest.fixture(scope="session", autouse=True)
def _e2e_session_setup():
    if not _is_e2e_enabled():
        yield
        return
    with contextlib.suppress(Exception):
        cleanup_e2e_users()
    for email in E2E_TEST_USERS:
        with contextlib.suppress(Exception):
            cleanup_e2e_contacts(email)
    try:
        admin = admin_session()
        admin.post(f"{APP_URL}/admin/customers/2/purge", allow_redirects=True)
    except Exception:
        pass

    try:
        setup_e2e_users(APP_URL)
    except Exception as exc:
        pytest.exit(f"E2E user setup failed: {exc}")

    yield

    for email in E2E_TEST_USERS:
        with contextlib.suppress(Exception):
            cleanup_e2e_contacts(email)
    with contextlib.suppress(Exception):
        cleanup_e2e_users()


@pytest.fixture(scope="session")
def app_url():
    return os.environ.get("E2E_APP_URL", APP_URL)


@pytest.fixture(scope="session")
def mail_api_url():
    return os.environ.get("E2E_MAIL_API_URL", MAIL_API_URL)


@pytest.fixture(scope="function")
def user_session():
    return login_session("e2e-test@test.localhost", E2E_DEFAULT_PASSWORD)


@pytest.fixture(scope="function")
def user_b_session():
    return login_session("e2e-test2@test.localhost", E2E_DEFAULT_PASSWORD)


@pytest.fixture(scope="function")
def admin_sess():
    return admin_session()


@pytest.fixture(scope="function")
def manager_session():
    return login_session("manager@test.localhost", E2E_DEFAULT_PASSWORD)


@pytest.fixture(scope="function")
def user_account_id(app_url, user_session):
    return get_account_id(app_url, user_session)
