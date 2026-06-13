import re
import uuid

import pytest
import requests

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import E2E_DEFAULT_PASSWORD


def _create_api_token(app_url, user_session):
    r = user_session.get(f"{app_url}/app/mail/settings/api", allow_redirects=True)
    if "Enable API Access" in r.text or "enable" in r.text.lower():
        user_session.post(
            f"{app_url}/app/mail/settings/api/enable",
            data={"password": E2E_DEFAULT_PASSWORD},
            allow_redirects=True,
        )

    tag = uuid.uuid4().hex[:8]
    r = user_session.post(
        f"{app_url}/app/mail/settings/api/tokens/create",
        data={
            "token_name": f"E2E API Test {tag}",
            "scope_mail_read": "on",
            "scope_contacts_read": "on",
            "scope_contacts_write": "on",
            "scope_calendar_read": "on",
            "scope_docs_read": "on",
        },
        allow_redirects=True,
    )
    assert r.status_code == 200
    token_match = re.search(r"font-mono[^>]*>([^<]+)</div>", r.text)
    assert token_match, "API token not found in response"
    return token_match.group(1).strip()


def _api_get(session_or_url, path, token=None, **kwargs):
    if token:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        kwargs["headers"] = headers
    if isinstance(session_or_url, requests.Session):
        return session_or_url.get(path, **kwargs)
    return requests.get(f"{session_or_url}{path}", **kwargs)


def _api_post(session_or_url, path, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Content-Type", "application/json")
    kwargs["headers"] = headers
    if isinstance(session_or_url, requests.Session):
        return session_or_url.post(path, **kwargs)
    return requests.post(f"{session_or_url}{path}", **kwargs)


@skip_if_no_services
class TestAPIAuth:
    def test_no_token_returns_auth_missing(self, app_url):
        r = requests.get(f"{app_url}/api/v1/accounts")
        assert r.status_code == 401
        body = r.json()
        assert body.get("error", {}).get("code") == "AUTH_MISSING"

    def test_invalid_token_returns_auth_invalid(self, app_url):
        r = requests.get(
            f"{app_url}/api/v1/accounts",
            headers={"Authorization": "Bearer lr_invalidtoken12345"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body.get("error", {}).get("code") == "AUTH_INVALID"


@skip_if_no_services
class TestAPIEndpoints:
    @pytest.fixture()
    def api_token(self, app_url, user_session):
        return _create_api_token(app_url, user_session)

    def test_list_accounts(self, app_url, api_token):
        r = _api_get(app_url, "/api/v1/accounts", api_token)
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) >= 1
        acct = body["data"][0]
        assert "id" in acct
        assert "email" in acct
        assert "auth_type" in acct

    def test_list_mail_folders(self, app_url, api_token):
        r = _api_get(app_url, "/api/v1/mail/folders", api_token)
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        if body["data"]:
            folder = body["data"][0]
            assert "name" in folder
            assert "unread_count" in folder

    def test_list_contacts(self, app_url, api_token):
        r = _api_get(app_url, "/api/v1/contacts", api_token)
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        if body["data"]:
            contact = body["data"][0]
            for field in ("id", "uid", "fn"):
                assert field in contact, f"Missing field '{field}' in contact"

    def test_create_contact(self, app_url, api_token):
        tag = uuid.uuid4().hex[:8]
        r = _api_post(
            app_url,
            "/api/v1/contacts",
            api_token,
            json={
                "fn": f"E2E API Contact {tag}",
                "email_work": f"e2e-api-{tag}@test.localhost",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert "data" in body
        assert "uid" in body["data"]
        assert "fn" in body["data"]

    def test_list_calendars(self, app_url, api_token):
        r = _api_get(app_url, "/api/v1/calendar/calendars", api_token)
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        assert isinstance(body["data"], list)

    def test_response_format_has_data_key(self, app_url, api_token):
        r = _api_get(app_url, "/api/v1/accounts", api_token)
        body = r.json()
        assert "data" in body or "error" in body

        r = _api_get(app_url, "/api/v1/contacts", api_token)
        body = r.json()
        has_data = "data" in body
        has_paginated = "data" in body and "pagination" in body
        assert has_data or has_paginated or "error" in body
