from __future__ import annotations

import json

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from unittest.mock import patch, MagicMock

from app.shared.db import db as _db
from app.shared.models.core import User, Domain, CustomerAccount
from app.shared.keys import set_user_key, clear_user_key
from app.shared.oauth import get_public_key
from app.api.token_service import generate_dek, wrap_dek_with_credential


NGROK_HOST = "frostlike-spore-arrange.ngrok-free.dev"
NGROK_ISSUER = f"https://{NGROK_HOST}"


def _generate_pkce():
    import base64, hashlib, secrets
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _register_client(flask_client):
    redirect_uri = "https://chatgpt.com/connector/oauth/test123"
    resp = flask_client.post(
        "/oauth/register",
        json={
            "client_name": "ChatGPT Connector",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
        },
    )
    return json.loads(resp.data), resp.status_code


def _run_oauth_flow(flask_client, user_id, account_id, scope="mail.read mail.write"):
    reg_data, reg_status = _register_client(flask_client)
    assert reg_status == 201
    client_id = reg_data["client_id"]
    redirect_uri = "https://chatgpt.com/connector/oauth/test123"
    verifier, challenge = _generate_pkce()
    resource = NGROK_ISSUER

    with flask_client.session_transaction() as sess:
        sess["role"] = "customer"
        sess["user_id"] = user_id
        sess["active_account_id"] = account_id

    resp = flask_client.post("/oauth/authorize", data={
        "action": "approve",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "scopes": scope.split(),
        "resource": resource,
        "state": "test-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "response_type": "code",
    })
    assert resp.status_code == 302
    location = resp.headers["Location"]
    code = location.split("code=")[1].split("&")[0]

    resp = flask_client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    })
    assert resp.status_code == 200
    token_data = json.loads(resp.data)
    return token_data["access_token"], client_id, resource


@pytest.fixture(autouse=True)
def _set_server_name(app):
    app.config["SERVER_NAME"] = NGROK_HOST
    yield
    app.config["SERVER_NAME"] = ""


@pytest.fixture()
def oauth_user(app, _clean_db):
    user_id = None
    account_id = None
    with app.app_context():
        user = User(email="ngrok@example.com", role="customer", is_active=True)
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain(
            name="example.com",
            is_active=True,
            status="active",
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        _db.session.add(domain)
        _db.session.flush()

        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address="ngrok@example.com",
            auth_type="password",
            username="ngrok@example.com",
            cache_db_path="",
            api_enabled=True,
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    yield user_id, account_id

    clear_user_key(user_id)


@pytest.fixture()
def oauth_user_no_api(app, _clean_db):
    user_id = None
    account_id = None
    cred_key = "ab" * 32
    with app.app_context():
        user = User(email="ngrok-noapi@example.com", role="customer", is_active=True)
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()
        user_id = user.id

        domain = Domain(
            name="example.com",
            is_active=True,
            status="active",
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        _db.session.add(domain)
        _db.session.flush()

        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address="ngrok-noapi@example.com",
            auth_type="password",
            username="ngrok-noapi@example.com",
            cache_db_path="",
            api_enabled=False,
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, cred_key)

    yield user_id, account_id, cred_key

    clear_user_key(user_id)


class TestNgrokChatGPTFlow:
    """Full ChatGPT + ngrok flow: discovery, OAuth, REST API, MCP.

    Simulates SERVER_NAME set to a ngrok domain (as an engineer would
    configure), verifying the entire end-to-end workflow.
    """

    def test_server_name_is_ngrok_host(self, app):
        assert app.config["SERVER_NAME"] == NGROK_HOST

    def test_discovery_returns_ngrok_issuer(self, app, client):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["issuer"] == NGROK_ISSUER
        assert data["token_endpoint"] == f"{NGROK_ISSUER}/oauth/token"
        assert data["registration_endpoint"] == f"{NGROK_ISSUER}/oauth/register"

        resp = client.get("/.well-known/openid-configuration")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["issuer"] == NGROK_ISSUER

    def test_client_registration(self, app, client):
        data, status = _register_client(client)
        assert status == 201
        assert "client_id" in data

    def test_full_oauth_flow_issuing_ngrok_jwt(self, app, client, oauth_user):
        user_id, account_id = oauth_user
        access_token, _, resource = _run_oauth_flow(client, user_id, account_id)

        with app.app_context():
            pub_key_bytes = get_public_key(app)
        pub_key = serialization.load_pem_public_key(pub_key_bytes)
        payload = pyjwt.decode(
            access_token, pub_key, algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert payload["iss"] == NGROK_ISSUER
        assert payload["sub"] == str(user_id)
        assert payload["aud"] == resource

    def test_jwt_accepted_by_rest_api(self, app, client, oauth_user):
        user_id, account_id = oauth_user
        access_token, _, _ = _run_oauth_flow(
            client, user_id, account_id, scope="mail.read",
        )

        resp = client.get(
            "/api/v1/mail/folders",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code != 401, "JWT should be accepted by REST API auth"

    def test_api_auto_enabled_during_oauth(self, app, client, oauth_user_no_api):
        user_id, account_id, cred_key = oauth_user_no_api

        with app.app_context():
            account = _db.session.get(CustomerAccount, account_id)
            assert account.api_enabled is False

        access_token, _, _ = _run_oauth_flow(
            client, user_id, account_id, scope="mail.read",
        )

        with app.app_context():
            account = _db.session.get(CustomerAccount, account_id)
            assert account.api_enabled is True
            assert account.dek_wrapped_cred is not None

    def test_mcp_initialize_with_ngrok_jwt(self, app, client, oauth_user):
        user_id, account_id = oauth_user
        access_token, _, _ = _run_oauth_flow(
            client, user_id, account_id, scope="mail.read mail.write",
        )

        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            asgi_app = create_asgi_app()

        from starlette.testclient import TestClient
        with TestClient(asgi_app) as mcp_client:
            resp = mcp_client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "openai-mcp/1.0.0 (ChatGPT)", "version": "1.0"},
                    },
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Host": NGROK_HOST,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["jsonrpc"] == "2.0"
            assert "result" in data
            assert data["result"]["serverInfo"]["name"] == "locoroomail"

    def test_mcp_asgi_proxies_oauth_discovery(self, app, client, oauth_user):
        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            asgi_app = create_asgi_app()

        from starlette.testclient import TestClient
        with TestClient(asgi_app) as mcp_client:
            resp = mcp_client.get(
                "/.well-known/oauth-authorization-server",
                headers={"Host": NGROK_HOST},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["issuer"] == NGROK_ISSUER

    def test_mcp_tool_call_accepts_ngrok_jwt(self, app, client, oauth_user):
        user_id, account_id = oauth_user
        access_token, _, _ = _run_oauth_flow(
            client, user_id, account_id, scope="mail.read mail.write",
        )

        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            asgi_app = create_asgi_app()

        from starlette.testclient import TestClient
        with TestClient(asgi_app) as mcp_client:
            mcp_client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "openai-mcp/1.0.0 (ChatGPT)", "version": "1.0"},
                    },
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Host": NGROK_HOST,
                },
            )

            mcp_client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )

            tools_resp = mcp_client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Host": NGROK_HOST,
                },
            )
            assert tools_resp.status_code == 200
            tools_data = tools_resp.json()
            assert "result" in tools_data
            tool_names = [t["name"] for t in tools_data["result"]["tools"]]
            assert "mail_list_folders" in tool_names


class TestNgrokDevModeRelaxations:
    """Tests for dev-mode relaxations when SERVER_NAME is empty."""

    def test_server_name_empty_in_dev(self, app):
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = ""
            assert app.config["SERVER_NAME"] == ""
            assert app.config.get("APP_ENV") == "development"
        finally:
            app.config["SERVER_NAME"] = saved

    def test_authorize_allows_empty_server_name_in_dev(self, app, client, oauth_user):
        user_id, account_id = oauth_user
        app.config["SERVER_NAME"] = NGROK_HOST

        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        _, challenge = _generate_pkce()

        app.config["SERVER_NAME"] = ""

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        resp = client.get("/oauth/authorize", query_string={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "mail.read",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": NGROK_ISSUER,
            "state": "test",
        })
        assert resp.status_code == 200
        assert b"Authorize" in resp.data or b"authorize" in resp.data

        app.config["SERVER_NAME"] = NGROK_HOST


class TestSafeRedirectWithNgrok:
    """Tests for _is_safe_redirect_url accepting ngrok domains in dev."""

    def test_accepts_ngrok_free_dev_domain(self, app):
        from app.modules.mail.controllers.auth import _is_safe_redirect_url
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = ""
            url = f"https://{NGROK_HOST}/oauth/authorize?code=abc"
            with app.test_request_context("/", headers={"Host": NGROK_HOST}):
                assert _is_safe_redirect_url(url) is True
        finally:
            app.config["SERVER_NAME"] = saved

    def test_accepts_matching_request_host(self, app):
        from app.modules.mail.controllers.auth import _is_safe_redirect_url
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = ""
            url = "https://my-custom-host.local:5001/oauth/authorize"
            with app.test_request_context("/", headers={"Host": "my-custom-host.local:5001"}):
                assert _is_safe_redirect_url(url) is True
        finally:
            app.config["SERVER_NAME"] = saved

    def test_rejects_unrelated_host(self, app):
        from app.modules.mail.controllers.auth import _is_safe_redirect_url
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = ""
            url = "https://evil.com/oauth/authorize"
            with app.test_request_context("/", headers={"Host": NGROK_HOST}):
                assert _is_safe_redirect_url(url) is False
        finally:
            app.config["SERVER_NAME"] = saved

    def test_accepts_relative_path(self, app):
        from app.modules.mail.controllers.auth import _is_safe_redirect_url
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = ""
            with app.test_request_context("/", headers={"Host": NGROK_HOST}):
                assert _is_safe_redirect_url("/oauth/authorize?code=abc") is True
        finally:
            app.config["SERVER_NAME"] = saved

    def test_rejects_javascript_scheme(self, app):
        from app.modules.mail.controllers.auth import _is_safe_redirect_url
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = ""
            with app.test_request_context("/", headers={"Host": NGROK_HOST}):
                assert _is_safe_redirect_url("javascript:alert(1)") is False
        finally:
            app.config["SERVER_NAME"] = saved

    def test_different_ngrok_domain_accepted(self, app):
        from app.modules.mail.controllers.auth import _is_safe_redirect_url
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = ""
            url = "https://other-tunnel.ngrok-free.dev/oauth/authorize"
            with app.test_request_context("/", headers={"Host": NGROK_HOST}):
                assert _is_safe_redirect_url(url) is True
        finally:
            app.config["SERVER_NAME"] = saved

    def test_server_name_takes_priority_over_ngrok(self, app):
        from app.modules.mail.controllers.auth import _is_safe_redirect_url
        saved = app.config["SERVER_NAME"]
        try:
            app.config["SERVER_NAME"] = "mail.example.com"
            url = f"https://{NGROK_HOST}/oauth/authorize"
            with app.test_request_context("/", headers={"Host": NGROK_HOST}):
                assert _is_safe_redirect_url(url) is False

            url = "https://mail.example.com/oauth/authorize"
            with app.test_request_context("/", headers={"Host": NGROK_HOST}):
                assert _is_safe_redirect_url(url) is True
        finally:
            app.config["SERVER_NAME"] = saved
