import uuid

import pytest
import requests

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import (
    E2E_DEFAULT_PASSWORD,
    login_session,
    admin_session,
    mailapi_create_user,
    mailapi_user_exists,
)


@skip_if_no_services
class TestCustomerLogin:
    def test_login_redirects_to_mail(self, app_url, user_session):
        r = user_session.get(f"{app_url}/app/auth/check")
        assert r.status_code == 200

    def test_login_with_wrong_password_shows_error(self, app_url):
        s = requests.Session()
        r = s.post(
            f"{app_url}/app/login",
            data={"email": "e2e-test@test.localhost", "password": "wrongpassword"},
            allow_redirects=True,
        )
        assert "login" in r.url
        assert "IMAP authentication failed" in r.text or "Invalid" in r.text or "Domain not enabled" in r.text

    def test_logout_clears_session(self, app_url):
        s = login_session("e2e-test@test.localhost", E2E_DEFAULT_PASSWORD)
        r = s.get(f"{app_url}/app/logout", allow_redirects=False)
        assert r.status_code in (302, 303)
        r = s.get(f"{app_url}/app/mail/", allow_redirects=False)
        assert r.status_code in (302, 303)
        assert "login" in r.headers.get("Location", "")


@skip_if_no_services
class TestAdminLogin:
    def test_admin_login_redirects_to_dashboard(self, app_url, admin_sess):
        r = admin_sess.get(f"{app_url}/admin/")
        assert r.status_code == 200
        assert "admin" in r.text.lower()

    def test_admin_login_wrong_password(self, app_url):
        s = requests.Session()
        r = s.post(
            f"{app_url}/admin/login",
            data={"email": "admin@dev.test", "password": "wrongpassword"},
            allow_redirects=True,
        )
        assert "Invalid credentials" in r.text

    def test_admin_logout(self, app_url):
        s = admin_session()
        r = s.get(f"{app_url}/logout", allow_redirects=False)
        assert r.status_code in (302, 303)
        r = s.get(f"{app_url}/admin/", allow_redirects=False)
        assert r.status_code in (302, 303)
        assert "login" in r.headers.get("Location", "")


@skip_if_no_services
class TestProtectedRoutes:
    def test_direct_url_redirects_to_login(self, app_url):
        s = requests.Session()
        r = s.get(f"{app_url}/app/mail/", allow_redirects=False)
        assert r.status_code in (302, 303)
        assert "login" in r.headers.get("Location", "")


@skip_if_no_services
@pytest.mark.skip(reason="Self-provisioning feature not yet implemented")
class TestSelfProvisioning:
    def test_new_user_auto_provisioned_on_login(self, app_url, admin_sess):
        email = f"e2e-provision-{uuid.uuid4().hex[:8]}@test.localhost"
        password = E2E_DEFAULT_PASSWORD

        mailapi_create_user(email, password)
        assert mailapi_user_exists(email)

        s = login_session(email, password)
        r = s.get(f"{app_url}/app/auth/check")
        assert r.status_code == 200

        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        assert email in r.text
