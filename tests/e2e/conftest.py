import os

import pytest

from tests.e2e.services import (
    APP_URL, MAIL_API_URL, check_services, login_session, admin_session,
    E2E_TEST_USERS, E2E_DEFAULT_PASSWORD, setup_e2e_users, cleanup_e2e_users,
    cleanup_e2e_contacts, get_account_id,
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
    try:
        cleanup_e2e_users()
    except Exception:
        pass
    for email in E2E_TEST_USERS:
        try:
            cleanup_e2e_contacts(email)
        except Exception:
            pass
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
        try:
            cleanup_e2e_contacts(email)
        except Exception:
            pass
    try:
        cleanup_e2e_users()
    except Exception:
        pass


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
