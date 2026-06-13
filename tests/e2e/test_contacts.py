import re
import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import carddav_report_contacts, wait_for


@skip_if_no_services
class TestContactList:
    def test_contact_list_loads(self, app_url, user_session):
        r = user_session.get(f"{app_url}/app/contacts/", allow_redirects=True)
        assert r.status_code == 200


@skip_if_no_services
class TestContactCRUD:
    def test_create_search_delete_contact(self, app_url, user_session):
        tag = uuid.uuid4().hex[:8]
        contact_name = f"E2E Contact {tag}"
        contact_email = f"e2e-crud-{tag}@test.localhost"
        phone_work = f"+1-555-{tag[:4]}"
        phone_cell = f"+1-555-{tag[4:]}"

        hrefs_before = len(carddav_report_contacts("e2e-test@test.localhost"))

        r = user_session.post(
            f"{app_url}/app/contacts/new",
            data={
                "fn": contact_name,
                "email_work": contact_email,
                "tel_work": phone_work,
                "tel_cell": phone_cell,
                "org": "E2E Corp",
            },
            allow_redirects=True,
        )
        assert r.status_code == 200

        wait_for(
            lambda: contact_name in user_session.get(f"{app_url}/app/contacts/").text,
            timeout=30,
        )

        r = user_session.get(f"{app_url}/app/contacts/", allow_redirects=True)
        assert r.status_code == 200
        assert contact_name in r.text
        assert "+" in r.text and tag[:4] in r.text

        wait_for(
            lambda: len(carddav_report_contacts("e2e-test@test.localhost")) > hrefs_before,
            timeout=15,
        )

        r = user_session.get(
            f"{app_url}/app/contacts/",
            params={"q": contact_name},
            allow_redirects=True,
        )
        assert r.status_code == 200
        assert contact_name in r.text

        r = user_session.get(f"{app_url}/app/contacts/", allow_redirects=True)
        detail_match = re.search(
            rf'href="/app/contacts/(\d+)/([^"]+)"[^>]*>\s*{re.escape(contact_name)}\s*</a>',
            r.text,
        )
        assert detail_match, f"Contact {contact_name} not found in list"
        account_id = detail_match.group(1)
        uid = detail_match.group(2)

        r = user_session.post(
            f"{app_url}/app/contacts/{account_id}/{uid}/delete",
            allow_redirects=True,
        )
        assert r.status_code == 200

        r = user_session.get(f"{app_url}/app/contacts/", allow_redirects=True)
        assert contact_name not in r.text
