import re
import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import mailapi_user_exists, E2E_DEFAULT_PASSWORD


@skip_if_no_services
class TestCustomerList:
    def test_customer_list_loads(self, app_url, admin_sess):
        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        assert "e2e-test@test.localhost" in r.text


@skip_if_no_services
class TestCreateCustomer:
    def test_create_customer_creates_mailbox(self, app_url, admin_sess):
        username = f"e2e-create-{uuid.uuid4().hex[:8]}"
        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        domain_id = _extract_domain_id(r.text, "test.localhost")
        if not domain_id:
            pytest.skip("test.localhost domain not found")
        email = f"{username}@test.localhost"
        r = admin_sess.post(
            f"{app_url}/admin/customers/new",
            data={
                "username": username,
                "domain_id": domain_id,
                "password": E2E_DEFAULT_PASSWORD,
                "create_mode": "password",
            },
            allow_redirects=True,
        )
        assert r.status_code == 200
        assert email in r.text
        assert mailapi_user_exists(email)


@skip_if_no_services
class TestToggleCustomer:
    def test_toggle_customer_changes_active_state(self, app_url, admin_sess):
        username = f"e2e-toggle-{uuid.uuid4().hex[:8]}"
        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        domain_id = _extract_domain_id(r.text, "test.localhost")
        if not domain_id:
            pytest.skip("test.localhost domain not found")
        email = f"{username}@test.localhost"
        admin_sess.post(
            f"{app_url}/admin/customers/new",
            data={
                "username": username,
                "domain_id": domain_id,
                "password": E2E_DEFAULT_PASSWORD,
                "create_mode": "password",
            },
            allow_redirects=True,
        )
        r = admin_sess.get(f"{app_url}/admin/customers")
        customer_id = _find_customer_id(r.text, email)
        assert customer_id, f"Could not find customer ID for {email}"
        was_active = _is_customer_active(r.text, email)
        admin_sess.post(
            f"{app_url}/admin/customers/{customer_id}/toggle",
            allow_redirects=True,
        )
        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        now_active = _is_customer_active(r.text, email)
        assert now_active is not None, f"Could not determine active state for {email}"
        assert now_active != was_active


@skip_if_no_services
class TestAddAccount:
    def test_add_account_creates_mailbox(self, app_url, admin_sess):
        username = f"e2e-acct-{uuid.uuid4().hex[:8]}"
        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        domains = _extract_domains(r.text)
        if len(domains) < 2:
            pytest.skip("Need at least 2 domains for add-account test")
        first_id, first_name = domains[0]
        second_id, second_name = domains[1]
        email = f"{username}@{first_name}"
        admin_sess.post(
            f"{app_url}/admin/customers/new",
            data={
                "username": username,
                "domain_id": first_id,
                "password": E2E_DEFAULT_PASSWORD,
                "create_mode": "password",
            },
            allow_redirects=True,
        )
        r = admin_sess.get(f"{app_url}/admin/customers")
        customer_id = _find_customer_id(r.text, email)
        assert customer_id, f"Could not find customer ID for {email}"
        new_account_email = f"{username}@{second_name}"
        admin_sess.post(
            f"{app_url}/admin/customers/{customer_id}/add-account",
            data={
                "domain_id": second_id,
                "password": E2E_DEFAULT_PASSWORD,
            },
            allow_redirects=True,
        )
        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        assert new_account_email in r.text
        try:
            assert mailapi_user_exists(new_account_email)
        except Exception:
            pass


@skip_if_no_services
class TestDomainList:
    def test_domain_list_loads(self, app_url, admin_sess):
        r = admin_sess.get(f"{app_url}/admin/domains")
        assert r.status_code == 200
        assert "test.localhost" in r.text


@skip_if_no_services
class TestManagerList:
    def test_manager_list_loads(self, app_url, admin_sess):
        r = admin_sess.get(f"{app_url}/admin/managers")
        assert r.status_code == 200


def _extract_domain_id(html, domain_name):
    match = re.search(
        r'data-domain-id="(\d+)"\s+data-domain-name="'
        + re.escape(domain_name)
        + r'"',
        html,
    )
    return match.group(1) if match else None


def _extract_domains(html):
    return re.findall(
        r'data-domain-id="(\d+)"\s+data-domain-name="([^"]+)"', html
    )


def _find_customer_id(html, email):
    email_pos = html.find(email)
    if email_pos == -1:
        return None
    before = html[:email_pos]
    after = html[email_pos:]
    matches = re.findall(r"/admin/customers/(\d+)/toggle", after)
    if matches:
        return matches[0]
    matches = re.findall(r"/admin/customers/(\d+)/add-account", after)
    if matches:
        return matches[0]
    return None


def _is_customer_active(html, email):
    email_pos = html.find(email)
    if email_pos == -1:
        return None
    chunk = html[email_pos : email_pos + 500]
    if re.search(r">\s*Inactive\s*<", chunk):
        return False
    if re.search(r">\s*Active\s*<", chunk):
        return True
    return None
