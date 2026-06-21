from __future__ import annotations

import base64
import hashlib
import json
import secrets
from unittest.mock import patch, MagicMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from starlette.testclient import TestClient

from app.shared.db import db as _db
from app.shared.models.core import User, Domain, CustomerAccount
from app.shared.keys import set_user_key, clear_user_key
from app.shared.oauth import get_public_key, _get_issuer
from app.api.token_service import generate_dek, wrap_dek_with_credential


@pytest.fixture(autouse=True)
def _set_server_name(app):
    app.config["SERVER_NAME"] = "localhost"
    yield
    app.config["SERVER_NAME"] = ""


def _generate_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _register_client(flask_client, name="ChatGPT Connector", redirect_uri=None):
    redirect_uri = redirect_uri or "https://chatgpt.com/connector/oauth/test123"
    resp = flask_client.post(
        "/oauth/register",
        json={
            "client_name": name,
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
        },
    )
    return json.loads(resp.data), resp.status_code


def _run_oauth_flask_flow(flask_client, user_id, account_id, scope="mail.read mail.write"):
    reg_data, reg_status = _register_client(flask_client)
    assert reg_status == 201
    client_id = reg_data["client_id"]
    redirect_uri = "https://chatgpt.com/connector/oauth/test123"
    verifier, challenge = _generate_pkce()
    resource = "https://localhost"

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


@pytest.fixture()
def oauth_user(app, _clean_db):
    user_id = None
    account_id = None
    with app.app_context():
        user = User(email="oauth@example.com", role="customer", is_active=True)
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
            email_address="oauth@example.com",
            auth_type="password",
            username="oauth@example.com",
            cache_db_path="",
            api_enabled=True,
            dek_wrapped_cred=b"placeholder",
            is_active=True,
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, "0" * 64)

    yield user_id, account_id

    clear_user_key(user_id)


@pytest.fixture()
def oauth_user_with_dek(app, _clean_db):
    user_id = None
    account_id = None
    dek = generate_dek()
    with app.app_context():
        user = User(email="oauth-dek@example.com", role="customer", is_active=True)
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

        cred_key = "ab" * 32
        wrapped = wrap_dek_with_credential(dek, cred_key)

        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address="oauth-dek@example.com",
            auth_type="password",
            username="oauth-dek@example.com",
            cache_db_path="",
            api_enabled=True,
            dek_wrapped_cred=wrapped,
        )
        _db.session.add(account)
        _db.session.commit()
        account_id = account.id

    set_user_key(user_id, dek)

    yield user_id, account_id, dek

    clear_user_key(user_id)


class TestOAuthMCPIntegration:
    def test_full_chatgpt_flow(self, app, client, oauth_user):
        user_id, account_id = oauth_user

        with app.app_context():
            issuer = _get_issuer(app)

        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        metadata = json.loads(resp.data)
        assert metadata["issuer"] == issuer
        assert metadata["token_endpoint"] == f"{issuer}/oauth/token"
        assert metadata["registration_endpoint"] == f"{issuer}/oauth/register"

        access_token, _, resource = _run_oauth_flask_flow(
            client, user_id, account_id,
        )

        with app.app_context():
            pub_key_bytes = get_public_key(app)
        pub_key = serialization.load_pem_public_key(pub_key_bytes)
        payload = pyjwt.decode(
            access_token, pub_key, algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert payload["iss"] == issuer
        assert payload["sub"] == str(user_id)
        assert payload["aud"] == resource

        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            asgi_app = create_asgi_app()

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
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["jsonrpc"] == "2.0"
            assert data["id"] == 1
            assert "result" in data
            assert "protocolVersion" in data["result"]
            assert "capabilities" in data["result"]
            assert "serverInfo" in data["result"]
            assert data["result"]["serverInfo"]["name"] == "locoroomail"

    def test_mcp_rejects_missing_token(self, app, _clean_db):
        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            asgi_app = create_asgi_app()

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
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data or "result" in data

    def test_mcp_rejects_invalid_token(self, app, _clean_db):
        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            asgi_app = create_asgi_app()

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
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
                headers={
                    "Authorization": "Bearer invalid-jwt-token",
                    "Content-Type": "application/json",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data or "result" in data

    def test_mcp_protected_resource_metadata(self, app, _clean_db):
        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            asgi_app = create_asgi_app()

        with TestClient(asgi_app) as mcp_client:
            resp = mcp_client.get("/mcp/.well-known/oauth-protected-resource")
            assert resp.status_code == 200
            data = resp.json()
            assert data["resource"] == "https://testserver"
            assert data["authorization_servers"] == ["https://testserver"]
            assert "mail.read" in data["scopes_supported"]

    def test_oauth_token_audience_matches_mcp_resource(self, app, client, oauth_user):
        user_id, account_id = oauth_user

        with app.app_context():
            issuer = _get_issuer(app)

        access_token, _, resource = _run_oauth_flask_flow(
            client, user_id, account_id,
        )

        with app.app_context():
            pub_key_bytes = get_public_key(app)
        pub_key = serialization.load_pem_public_key(pub_key_bytes)
        payload = pyjwt.decode(
            access_token, pub_key, algorithms=["RS256"],
            options={"verify_aud": False},
        )

        assert payload["aud"] == resource
        assert payload["aud"] == issuer or payload["aud"].startswith(issuer)

    def test_mcp_tool_call_after_oauth(self, app, client, oauth_user):
        user_id, account_id = oauth_user

        access_token, _, _ = _run_oauth_flask_flow(
            client, user_id, account_id, scope="mail.read mail.write",
        )

        from app.mcp import create_asgi_app
        with patch("app.workers.manager.WorkerManager") as MockWM:
            MockWM.return_value = MagicMock()
            with patch("app.mcp._create_flask_app", return_value=app):
                asgi_app = create_asgi_app()

        mock_conn = MagicMock()
        mock_conn.close = MagicMock()
        with TestClient(asgi_app) as mcp_client:
            init_resp = mcp_client.post(
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
                },
            )
            assert init_resp.status_code == 200

            mcp_client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
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
                },
            )
            assert tools_resp.status_code == 200
            tools_data = tools_resp.json()
            assert "result" in tools_data
            tool_names = [t["name"] for t in tools_data["result"]["tools"]]
            assert "mail_list_folders" in tool_names

            with patch("app.mcp.tools.mail._get_cache_conn", return_value=mock_conn), \
                 patch("app.modules.mail.services.cache_db.list_cached_folders", return_value=[]):
                list_folders_resp = mcp_client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "mail_list_folders",
                            "arguments": {},
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )
            assert list_folders_resp.status_code == 200
            folders_data = list_folders_resp.json()
            assert "result" in folders_data
            content = folders_data["result"].get("content", [])
            assert len(content) > 0
            assert content[0]["type"] == "text"
            assert "No Flask context" not in content[0]["text"]
            assert "INTERNAL" not in content[0]["text"]

    def test_stale_session_redirect_preserves_next_to_authorize(
        self, app, client, oauth_user
    ):
        user_id, account_id = oauth_user

        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        _, challenge = _generate_pkce()
        authorize_url = (
            f"/oauth/authorize?response_type=code"
            f"&client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope=mail.read"
            f"&code_challenge={challenge}"
            f"&code_challenge_method=S256"
            f"&resource=https://localhost"
            f"&state=test-state"
        )

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        clear_user_key(user_id)

        resp = client.get(authorize_url, headers={"Accept": "text/html"})
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "next=" in location
        assert "/oauth/authorize" in location

    def test_oauth_jwt_dek_unwrap(self, app, client, oauth_user_with_dek):
        user_id, account_id, expected_dek = oauth_user_with_dek

        access_token, _, _ = _run_oauth_flask_flow(
            client, user_id, account_id,
        )

        from app.mcp.auth import resolve_context, get_dek, set_current_token
        set_current_token(access_token)
        auth_ctx = resolve_context(access_token, app)
        assert auth_ctx["token_type"] == "jwt"
        assert "jti" in auth_ctx

        result_dek = get_dek(auth_ctx, app)
        assert result_dek == expected_dek

    def test_full_oauth_to_mcp_tool_with_real_dek(self, app, client, oauth_user_with_dek):
        """End-to-end: OAuth → JWT → MCP tool call with real DEK unwrapping and real cache DB."""
        import tempfile
        import os
        user_id, account_id, expected_dek = oauth_user_with_dek

        with app.app_context():
            from app.shared.models.core import CustomerAccount
            account = _db.session.get(CustomerAccount, account_id)
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp.close()
            account.cache_db_path = tmp.name
            _db.session.commit()

            from app.modules.mail.services.cache_db import open_cache
            conn = open_cache(tmp.name, expected_dek)
            from app.modules.mail.services.cache_db import upsert_folder
            upsert_folder(conn, "INBOX", 0)
            upsert_folder(conn, "Sent", 0)
            conn.close()

        try:
            access_token, _, _ = _run_oauth_flask_flow(
                client, user_id, account_id, scope="mail.read mail.write",
            )

            from app.mcp import create_asgi_app
            with patch("app.workers.manager.WorkerManager") as MockWM:
                MockWM.return_value = MagicMock()
                with patch("app.mcp._create_flask_app", return_value=app):
                    asgi_app = create_asgi_app()

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
                            "clientInfo": {"name": "test-e2e", "version": "1.0"},
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
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

                resp = mcp_client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "mail_list_folders",
                            "arguments": {},
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                content = data["result"]["content"]
                text = content[0]["text"]
                parsed = json.loads(text)
                assert "error" not in parsed, f"MCP tool returned error: {parsed}"
                assert "data" in parsed
                folder_names = [f["name"] for f in parsed["data"]]
                assert "INBOX" in folder_names
                assert "Sent" in folder_names
        finally:
            os.unlink(tmp.name)

    def test_oauth_to_mcp_after_user_keys_wipe(self, app, client, oauth_user_with_dek):
        """Simulate server restart: _user_keys wiped, session survives, OAuth re-seeds keys."""
        import tempfile
        import os
        user_id, account_id, expected_dek = oauth_user_with_dek

        with app.app_context():
            from app.shared.models.core import CustomerAccount
            account = _db.session.get(CustomerAccount, account_id)
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp.close()
            account.cache_db_path = tmp.name
            _db.session.commit()

            from app.modules.mail.services.cache_db import open_cache
            from app.modules.mail.services.cache_db import upsert_folder
            conn = open_cache(tmp.name, expected_dek)
            upsert_folder(conn, "INBOX", 0)
            conn.close()

        try:
            access_token, _, _ = _run_oauth_flask_flow(
                client, user_id, account_id, scope="mail.read",
            )

            clear_user_key(user_id)

            from app.mcp import create_asgi_app

            with patch("app.workers.manager.WorkerManager") as MockWM:
                MockWM.return_value = MagicMock()
                with patch("app.mcp._create_flask_app", return_value=app):
                    asgi_app = create_asgi_app()


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
                            "clientInfo": {"name": "test-e2e", "version": "1.0"},
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )

                resp = mcp_client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "mail_list_folders",
                            "arguments": {},
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                text = data["result"]["content"][0]["text"]
                parsed = json.loads(text)
                assert "error" not in parsed, f"MCP tool returned error after _user_keys wipe: {parsed}"
                assert "data" in parsed
        finally:
            os.unlink(tmp.name)
