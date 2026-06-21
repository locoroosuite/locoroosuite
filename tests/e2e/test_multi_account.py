import re

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import (
    mailapi_user_exists,
    wait_for,
    E2E_DEFAULT_PASSWORD,
)


def _extract_domains(html):
    return re.findall(
        r'data-domain-id="(\d+)"\s+data-domain-name="([^"]+)"', html
    )


def _find_customer_id(html, email):
    email_pos = html.find(email)
    if email_pos == -1:
        return None
    chunk = html[email_pos : email_pos + 5000]
    match = re.search(r"/admin/customers/(\d+)/toggle", chunk)
    return match.group(1) if match else None


@skip_if_no_services
class TestMultiAccount:
    def test_account_switcher_appears_everywhere(self, app_url, admin_sess, user_session, user_account_id):
        r = admin_sess.get(f"{app_url}/admin/customers")
        assert r.status_code == 200
        domains = _extract_domains(r.text)
        if len(domains) < 2:
            pytest.skip("Need at least 2 domains for multi-account test")

        customer_id = _find_customer_id(r.text, "e2e-test@test.localhost")
        assert customer_id, "Could not find customer ID for e2e-test@test.localhost"

        first_domain_id, first_domain_name = domains[0]
        second_domain_id, second_domain_name = domains[1]
        test_username = "e2e-test@test.localhost".split("@")[0]
        new_account_email = f"{test_username}@{second_domain_name}"

        existing_account = re.search(
            rf'{re.escape(new_account_email)}.*?/admin/customers/(\d+)/',
            r.text,
        )
        if not existing_account:
            admin_sess.post(
                f"{app_url}/admin/customers/{customer_id}/add-account",
                data={
                    "domain_id": second_domain_id,
                    "password": E2E_DEFAULT_PASSWORD,
                },
                allow_redirects=True,
            )
            try:
                wait_for(
                    lambda: mailapi_user_exists(new_account_email),
                    timeout=10,
                )
            except Exception:
                pass

        r = user_session.get(f"{app_url}/app/mail/", allow_redirects=True)
        assert r.status_code == 200
        assert 'name="account_id"' in r.text

        r = user_session.get(f"{app_url}/app/contacts/", allow_redirects=True)
        assert r.status_code == 200
        assert 'name="account_id"' in r.text

        r = user_session.get(f"{app_url}/app/calendar/", allow_redirects=True)
        assert r.status_code == 200
        assert 'name="account_id"' in r.text

        r = user_session.get(f"{app_url}/app/docs/", allow_redirects=True)
        assert r.status_code == 200
        assert 'name="account_id"' in r.text

        options = re.findall(
            r'<option\s+value="(\d+)"[^>]*>([^<]+)</option>',
            r.text,
        )
        second_account_id = None
        for opt_id, opt_email in options:
            if opt_email.strip() == new_account_email:
                second_account_id = opt_id
                break

        if second_account_id:
            r = user_session.post(
                f"{app_url}/app/mail/accounts/active",
                data={
                    "account_id": second_account_id,
                    "next": "/app/contacts/",
                },
                allow_redirects=True,
            )
            assert r.status_code == 200

            r = user_session.get(f"{app_url}/app/contacts/", allow_redirects=True)
            assert r.status_code == 200

            r = user_session.get(f"{app_url}/app/calendar/", allow_redirects=True)
            assert r.status_code == 200

            r = user_session.get(f"{app_url}/app/docs/", allow_redirects=True)
            assert r.status_code == 200

            r = user_session.post(
                f"{app_url}/app/mail/accounts/active",
                data={
                    "account_id": str(user_account_id),
                    "next": "/app/mail/",
                },
                allow_redirects=True,
            )
            assert r.status_code == 200
