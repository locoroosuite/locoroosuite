from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets

import pytest
import requests

from tests.e2e.services import E2E_DEFAULT_PASSWORD

FLASK_URL = os.environ.get("E2E_FLASK_URL", "http://localhost:5001")
MCP_URL = os.environ.get("E2E_MCP_URL", "http://localhost:8001")


def _services_reachable() -> bool:
    try:
        r1 = requests.get(f"{FLASK_URL}/app/login", timeout=3)
        r2 = requests.get(f"{MCP_URL}/mcp/status", timeout=3)
        return r1.status_code == 200 and r2.status_code == 200
    except Exception:
        return False


skip_if_no_services = pytest.mark.skipif(
    not _services_reachable(),
    reason="E2E services not running. Start with: make dev-up",
)


def _generate_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register_oauth_client(flask_url: str) -> str:
    resp = requests.post(f"{flask_url}/oauth/register", json={
        "client_name": "E2E Test Client",
        "redirect_uris": ["https://chatgpt.com/connector/oauth/e2e-test"],
        "token_endpoint_auth_method": "none",
    })
    assert resp.status_code == 201, f"OAuth client registration failed: {resp.status_code} {resp.text}"
    return resp.json()["client_id"]


def _run_oauth_flow(flask_url: str, email: str, password: str, client_id: str, scope: str = "mail.read"):
    s = requests.Session()
    login = s.post(f"{flask_url}/app/login", data={"email": email, "password": password}, allow_redirects=True)
    assert login.status_code == 200, f"Login failed: {login.status_code}"

    verifier, challenge = _generate_pkce()
    redirect_uri = "https://chatgpt.com/connector/oauth/e2e-test"

    authorize_post = s.post(f"{flask_url}/oauth/authorize", data={
        "action": "approve",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "scopes": scope.split(),
        "resource": "https://localhost",
        "state": "e2e-test",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "response_type": "code",
    }, allow_redirects=False)
    assert authorize_post.status_code == 302, f"Authorize failed: {authorize_post.status_code}"
    location = authorize_post.headers["Location"]
    assert "code=" in location, f"No code in redirect: {location}"
    code = location.split("code=")[1].split("&")[0]

    token_resp = requests.post(f"{flask_url}/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    })
    assert token_resp.status_code == 200, f"Token exchange failed: {token_resp.status_code} {token_resp.text}"
    return token_resp.json()["access_token"]


def _mcp_initialize(mcp_url: str, access_token: str):
    resp = requests.post(f"{mcp_url}/mcp", json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test/1.0", "version": "1.0"},
        },
    }, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    })
    assert resp.status_code == 200

    requests.post(f"{mcp_url}/mcp", json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    })


def _mcp_tool_call(mcp_url: str, access_token: str, tool_name: str, arguments: dict | None = None) -> dict:
    resp = requests.post(f"{mcp_url}/mcp", json={
        "jsonrpc": "2.0",
        "id": secrets.randbits(16),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    })
    assert resp.status_code == 200
    data = resp.json()
    content = data.get("result", {}).get("content", [])
    assert content, f"No content in MCP response: {json.dumps(data)[:500]}"
    return json.loads(content[0]["text"])


@pytest.fixture(scope="module")
def oauth_token():
    client_id = _register_oauth_client(FLASK_URL)
    return _run_oauth_flow(
        FLASK_URL,
        "e2e-test@test.localhost",
        E2E_DEFAULT_PASSWORD,
        client_id,
        scope="mail.read mail.write contacts.read contacts.write calendar.read calendar.write docs.read docs.write",
    )


@pytest.fixture(scope="module", autouse=True)
def mcp_init(oauth_token):
    _mcp_initialize(MCP_URL, oauth_token)


@skip_if_no_services
class TestOAuthMCPFlow:
    def test_mail_list_folders(self, oauth_token):
        result = _mcp_tool_call(MCP_URL, oauth_token, "mail_list_folders")
        assert "error" not in result, f"mail_list_folders error: {result['error']}"
        assert "data" in result
        assert isinstance(result["data"], list)

    def test_calendar_list_calendars(self, oauth_token):
        result = _mcp_tool_call(MCP_URL, oauth_token, "calendar_list_calendars")
        assert "error" not in result, f"calendar_list_calendars error: {result['error']}"
        assert "data" in result
        assert isinstance(result["data"], list)

    def test_contacts_list(self, oauth_token):
        result = _mcp_tool_call(MCP_URL, oauth_token, "contacts_list")
        assert "error" not in result, f"contacts_list error: {result['error']}"
        assert "data" in result

    def test_docs_list_documents(self, oauth_token):
        result = _mcp_tool_call(MCP_URL, oauth_token, "docs_list_documents")
        assert "error" not in result, f"docs_list_documents error: {result['error']}"
        assert "data" in result

    def test_tool_discovery(self, oauth_token):
        resp = requests.post(f"{MCP_URL}/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }, headers={
            "Authorization": f"Bearer {oauth_token}",
            "Content-Type": "application/json",
        })
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        expected = [
            "mail_list_folders", "mail_list_messages", "mail_search",
            "contacts_list", "contacts_search",
            "calendar_list_calendars", "calendar_list_events",
            "docs_list_documents",
        ]
        for name in expected:
            assert name in tool_names, f"Missing tool: {name}"

    def test_no_dek_after_fresh_oauth(self, oauth_token):
        result = _mcp_tool_call(MCP_URL, oauth_token, "mail_list_folders")
        assert "error" not in result, (
            f"NO_DEK or auth error after OAuth: {result.get('error', {}).get('code')} — "
            f"DEK provisioning failed. account.dek_wrapped_cred may be None."
        )


@skip_if_no_services
class TestOAuthReconnect:
    def test_second_oauth_connection_works(self):
        client_id = _register_oauth_client(FLASK_URL)
        token1 = _run_oauth_flow(FLASK_URL, "e2e-test@test.localhost", E2E_DEFAULT_PASSWORD, client_id)
        _mcp_initialize(MCP_URL, token1)
        result = _mcp_tool_call(MCP_URL, token1, "mail_list_folders")
        assert "error" not in result, f"First connection failed: {result.get('error')}"

        token2 = _run_oauth_flow(FLASK_URL, "e2e-test@test.localhost", E2E_DEFAULT_PASSWORD, client_id)
        _mcp_initialize(MCP_URL, token2)
        result2 = _mcp_tool_call(MCP_URL, token2, "mail_list_folders")
        assert "error" not in result2, f"Second connection failed: {result2.get('error')}"
