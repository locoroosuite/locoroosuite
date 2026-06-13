from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from flask import url_for

from app.shared.db import db as _db
from app.shared.models.oauth import OAuthAuthorizationCode, OAuthClient
from app.shared.oauth import get_public_key, _get_issuer


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


def _register_client(
    client, name="Test App", redirect_uri="https://chatgpt.com/connector/oauth/test123"
):
    resp = client.post(
        "/oauth/register",
        json={
            "client_name": name,
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
        },
    )
    return json.loads(resp.data), resp.status_code


def _full_oauth_params(client_id=None, redirect_uri=None, code_challenge=None, resource=None, scope="mail.read"):
    v, c = _generate_pkce()
    return {
        "client_id": client_id or "placeholder",
        "redirect_uri": redirect_uri or "https://chatgpt.com/connector/oauth/test123",
        "response_type": "code",
        "scope": scope,
        "resource": resource or "https://example.com",
        "state": "test-state",
        "code_challenge": code_challenge or c,
        "code_challenge_method": "S256",
    }, v, c


class TestOAuthMetadata:
    def test_authorization_server_metadata(self, app, client, _clean_db):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        issuer = _get_issuer(app)
        assert data["issuer"] == issuer
        assert data["authorization_endpoint"] == f"{issuer}/oauth/authorize"
        assert data["token_endpoint"] == f"{issuer}/oauth/token"
        assert data["registration_endpoint"] == f"{issuer}/oauth/register"
        assert data["jwks_uri"] == f"{issuer}/oauth/jwks.json"
        assert data["response_types_supported"] == ["code"]
        assert data["grant_types_supported"] == ["authorization_code"]
        assert data["token_endpoint_auth_methods_supported"] == ["none"]
        assert data["code_challenge_methods_supported"] == ["S256"]
        assert "mail.read" in data["scopes_supported"]
        assert "openid" in data["scopes_supported"]
        assert "RS256" in data["id_token_signing_alg_values_supported"]
        assert "public" in data["subject_types_supported"]

    def test_openid_configuration(self, app, client, _clean_db):
        resp = client.get("/.well-known/openid-configuration")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        issuer = _get_issuer(app)
        assert data["issuer"] == issuer
        assert data["authorization_endpoint"] == f"{issuer}/oauth/authorize"
        assert data["token_endpoint"] == f"{issuer}/oauth/token"
        assert data["jwks_uri"] == f"{issuer}/oauth/jwks.json"
        assert "openid" in data["scopes_supported"]
        assert "RS256" in data["id_token_signing_alg_values_supported"]
        assert "public" in data["subject_types_supported"]

    def test_protected_resource_metadata(self, app, client, _clean_db):
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        issuer = _get_issuer(app)
        assert data["resource"] == issuer
        assert data["authorization_servers"] == [issuer]
        assert data["bearer_methods_supported"] == ["header"]
        assert "mail.read" in data["scopes_supported"]

    def test_jwks_endpoint(self, app, client, _clean_db):
        resp = client.get("/oauth/jwks.json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "keys" in data
        assert len(data["keys"]) == 1
        key = data["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == "RS256"
        assert key["kid"] == "oauth-signing-key"
        assert "n" in key
        assert "e" in key


class TestOAuthClientRegistration:
    def test_register_client_success(self, app, client, _clean_db):
        data, status = _register_client(client)
        assert status == 201
        assert "client_id" in data
        assert data["client_name"] == "Test App"
        assert data["redirect_uris"] == ["https://chatgpt.com/connector/oauth/test123"]
        assert data["token_endpoint_auth_method"] == "none"

        with app.app_context():
            oauth_client = OAuthClient.query.filter_by(client_id=data["client_id"]).first()
            assert oauth_client is not None
            assert oauth_client.client_name == "Test App"

    def test_register_client_validates_redirect_uri(self, app, client, _clean_db):
        data, status = _register_client(
            client, redirect_uri="https://evil.example.com/callback"
        )
        assert status == 400
        assert data["error"] == "invalid_redirect_uri"
        assert "not allowed" in data["error_description"]

    def test_register_client_missing_fields(self, app, client, _clean_db):
        resp = client.post("/oauth/register", json={"redirect_uris": ["https://chatgpt.com/connector/oauth/test123"]})
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_client_metadata"

    def test_register_client_missing_redirect_uris(self, app, client, _clean_db):
        resp = client.post("/oauth/register", json={"client_name": "No URIs"})
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_client_metadata"

    def test_register_client_platform_redirect_uri(self, app, client, _clean_db):
        data, status = _register_client(
            client,
            redirect_uri="https://chatgpt.com/connector_platform_oauth_redirect",
        )
        assert status == 201
        assert "client_id" in data


class TestOAuthAuthorizeEndpoint:
    def test_authorize_requires_login(self, app, client, _clean_db):
        data, _, _ = _full_oauth_params()
        resp = client.get("/oauth/authorize", query_string=data)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_authorize_shows_consent(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]

        params, _, _ = _full_oauth_params(client_id=client_id, scope="mail.read mail.write")
        resp = client.get("/oauth/authorize", query_string=params)
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Authorize Test App" in body
        assert "Allow" in body
        assert "Deny" in body
        assert "Read your email messages" in body
        assert 'name="scopes"' in body
        assert 'value="mail.read"' in body

    def test_authorize_missing_params(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        resp = client.get("/oauth/authorize", query_string={"response_type": "code"})
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_request"
        assert "client_id is required" in data["error_description"]

    def test_authorize_invalid_response_type(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]

        params, _, _ = _full_oauth_params(client_id=client_id)
        params["response_type"] = "token"
        resp = client.get("/oauth/authorize", query_string=params)
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "response_type must be 'code'" in data["error_description"]

    def test_authorize_unknown_client(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        params, _, _ = _full_oauth_params(client_id="nonexistent-client-id")
        resp = client.get("/oauth/authorize", query_string=params)
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_client"

    def test_authorize_unregistered_redirect_uri(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]

        params, _, _ = _full_oauth_params(
            client_id=client_id,
            redirect_uri="https://chatgpt.com/connector/oauth/different",
        )
        resp = client.get("/oauth/authorize", query_string=params)
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "redirect_uri not registered" in data["error_description"]

    def test_authorize_missing_code_challenge(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]

        resp = client.get("/oauth/authorize", query_string={
            "client_id": client_id,
            "redirect_uri": "https://chatgpt.com/connector/oauth/test123",
            "response_type": "code",
            "scope": "mail.read",
            "resource": "https://example.com",
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "code_challenge is required" in data["error_description"]

    def test_authorize_missing_resource(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        v, c = _generate_pkce()

        resp = client.get("/oauth/authorize", query_string={
            "client_id": client_id,
            "redirect_uri": "https://chatgpt.com/connector/oauth/test123",
            "response_type": "code",
            "scope": "mail.read",
            "code_challenge": c,
            "code_challenge_method": "S256",
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "resource is required" in data["error_description"]


class TestOAuthAuthorizeConsent:
    def test_authorize_approve_issues_code(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"

        params, verifier, challenge = _full_oauth_params(
            client_id=client_id, redirect_uri=redirect_uri
        )
        resp = client.post("/oauth/authorize", data={
            "action": "approve",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "mail.read",
            "scopes": ["mail.read"],
            "resource": params["resource"],
            "state": "test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "code=" in location
        assert "state=test-state" in location
        assert location.startswith(redirect_uri)

    def test_authorize_deny_redirects_with_error(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"

        resp = client.post("/oauth/authorize", data={
            "action": "deny",
            "redirect_uri": redirect_uri,
            "state": "deny-state",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "error=access_denied" in location
        assert "state=deny-state" in location
        assert location.startswith(redirect_uri)


class TestOAuthTokenEndpoint:
    def _run_full_flow(self, app, client, user_id, account_id, scope="mail.read"):
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        verifier, challenge = _generate_pkce()
        resource = "https://example.com"

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        resp = client.post("/oauth/authorize", data={
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

        return client_id, redirect_uri, code, verifier, resource

    def test_token_exchange_success(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        client_id, redirect_uri, code, verifier, resource = self._run_full_flow(
            app, client, user_id, account_id
        )

        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 3600
        assert data["scope"] == "mail.read"
        assert "access_token" in data

        issuer = _get_issuer(app)
        pub_key_bytes = get_public_key(app)
        pub_key = serialization.load_pem_public_key(pub_key_bytes)
        payload = pyjwt.decode(
            data["access_token"],
            pub_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert payload["iss"] == issuer
        assert payload["sub"] == str(user_id)
        assert payload["scope"] == "mail.read"
        assert payload["aud"] == resource
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload
        assert payload["exp"] > payload["iat"]

    def test_token_exchange_invalid_code(self, app, client, _clean_db):
        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": "totally-invalid-code",
            "redirect_uri": "https://chatgpt.com/connector/oauth/test123",
            "client_id": "some-client",
            "code_verifier": "any",
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_grant"
        assert "Invalid authorization code" in data["error_description"]

    def test_token_exchange_code_reuse_rejected(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        client_id, redirect_uri, code, verifier, resource = self._run_full_flow(
            app, client, user_id, account_id
        )

        token_resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert token_resp.status_code == 200

        reuse_resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert reuse_resp.status_code == 400
        data = json.loads(reuse_resp.data)
        assert data["error"] == "invalid_grant"
        assert "already used" in data["error_description"]

    def test_token_exchange_pkce_failure(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        client_id, redirect_uri, code, verifier, resource = self._run_full_flow(
            app, client, user_id, account_id
        )

        wrong_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": wrong_verifier,
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_grant"
        assert "PKCE verification failed" in data["error_description"]

    def test_token_exchange_wrong_client_id(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        client_id, redirect_uri, code, verifier, resource = self._run_full_flow(
            app, client, user_id, account_id
        )

        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": "wrong-client-id",
            "code_verifier": verifier,
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_grant"
        assert "client_id mismatch" in data["error_description"]

    def test_token_exchange_wrong_redirect_uri(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        client_id, redirect_uri, code, verifier, resource = self._run_full_flow(
            app, client, user_id, account_id
        )

        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://chatgpt.com/connector/oauth/wrong",
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_grant"
        assert "redirect_uri mismatch" in data["error_description"]

    def test_token_unsupported_grant_type(self, app, client, _clean_db):
        resp = client.post("/oauth/token", data={
            "grant_type": "client_credentials",
            "code": "x",
            "redirect_uri": "x",
            "client_id": "x",
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "unsupported_grant_type"

    def test_token_missing_required_params(self, app, client, _clean_db):
        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_request"
        assert "Missing required parameters" in data["error_description"]

    def test_token_exchange_with_openid_scope_returns_id_token(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        client_id, redirect_uri, code, verifier, resource = self._run_full_flow(
            app, client, user_id, account_id, scope="openid mail.read"
        )

        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["scope"] == "openid mail.read"
        assert "access_token" in data
        assert "id_token" in data

        issuer = _get_issuer(app)
        pub_key_bytes = get_public_key(app)
        pub_key = serialization.load_pem_public_key(pub_key_bytes)
        id_claims = pyjwt.decode(
            data["id_token"],
            pub_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert id_claims["iss"] == issuer
        assert id_claims["sub"] == str(user_id)
        assert id_claims["aud"] == client_id
        assert id_claims["email"] == "test@example.com"
        assert "exp" in id_claims
        assert "iat" in id_claims
        assert "jti" in id_claims

    def test_token_exchange_without_openid_scope_no_id_token(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        client_id, redirect_uri, code, verifier, resource = self._run_full_flow(
            app, client, user_id, account_id, scope="mail.read"
        )

        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "id_token" not in data


class TestOAuthJWKSVerification:
    def test_jwt_verified_with_jwks(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client

        helper = TestOAuthTokenEndpoint()
        client_id, redirect_uri, code, verifier, resource = helper._run_full_flow(
            app, client, user_id, account_id
        )

        token_resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert token_resp.status_code == 200
        token_data = json.loads(token_resp.data)
        access_token = token_data["access_token"]

        jwks_resp = client.get("/oauth/jwks.json")
        assert jwks_resp.status_code == 200
        jwks = json.loads(jwks_resp.data)

        from jwt import PyJWKClient, PyJWK
        jwk_data = jwks["keys"][0]
        jwk_obj = PyJWK(jwk_data)

        payload = pyjwt.decode(
            access_token,
            jwk_obj.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert payload["iss"] == _get_issuer(app)
        assert payload["sub"] == str(user_id)
        assert payload["scope"] == "mail.read"


class TestSelectiveScopeApproval:
    def test_consent_page_shows_checkboxes(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]

        params, _, _ = _full_oauth_params(
            client_id=client_id,
            scope="mail.read mail.write contacts.read openid",
        )
        resp = client.get("/oauth/authorize", query_string=params)
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'value="mail.read"' in body
        assert 'value="mail.write"' in body
        assert 'value="contacts.read"' in body
        assert 'value="openid"' in body
        assert "disabled" in body

    def test_grant_subset_of_requested_scopes(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        verifier, challenge = _generate_pkce()
        resource = "https://example.com"

        requested_scope = "mail.read mail.write contacts.read contacts.write"

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        resp = client.post("/oauth/authorize", data={
            "action": "approve",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": requested_scope,
            "scopes": ["mail.read"],
            "resource": resource,
            "state": "test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "code=" in location
        code = location.split("code=")[1].split("&")[0]

        token_resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert token_resp.status_code == 200
        token_data = json.loads(token_resp.data)
        assert token_data["scope"] == "mail.read"

        pub_key_bytes = get_public_key(app)
        pub_key = serialization.load_pem_public_key(pub_key_bytes)
        payload = pyjwt.decode(
            token_data["access_token"],
            pub_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert payload["scope"] == "mail.read"

    def test_no_scopes_selected_returns_error(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        _, challenge = _generate_pkce()

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        resp = client.post("/oauth/authorize", data={
            "action": "approve",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "mail.read",
            "scopes": [],
            "resource": "https://example.com",
            "state": "test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        })
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["error"] == "invalid_request"
        assert "At least one scope" in data["error_description"]

    def test_cannot_grant_scope_not_requested(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        verifier, challenge = _generate_pkce()
        resource = "https://example.com"

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        resp = client.post("/oauth/authorize", data={
            "action": "approve",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "mail.read",
            "scopes": ["mail.read", "calendar.write"],
            "resource": resource,
            "state": "test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "code=" in location
        code = location.split("code=")[1].split("&")[0]

        token_resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert token_resp.status_code == 200
        token_data = json.loads(token_resp.data)
        assert token_data["scope"] == "mail.read"
        assert "calendar" not in token_data["scope"]

    def test_openid_always_included_when_requested(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        verifier, challenge = _generate_pkce()
        resource = "https://example.com"

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        resp = client.post("/oauth/authorize", data={
            "action": "approve",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid mail.read",
            "scopes": ["openid", "mail.read"],
            "resource": resource,
            "state": "test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        code = location.split("code=")[1].split("&")[0]

        token_resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert token_resp.status_code == 200
        token_data = json.loads(token_resp.data)
        assert "openid" in token_data["scope"]
        assert "mail.read" in token_data["scope"]
        assert "id_token" in token_data


class TestScopeNormalization:
    def test_dot_notation_converted_to_colon(self):
        from app.mcp.auth import _normalize_scopes
        assert _normalize_scopes(["mail.read"]) == ["mail:read"]
        assert _normalize_scopes(["mail.write"]) == ["mail:write"]
        assert _normalize_scopes(["contacts.read", "contacts.write"]) == ["contacts:read", "contacts:write"]

    def test_full_access_scope_expanded(self):
        from app.mcp.auth import _normalize_scopes
        result = _normalize_scopes(["mail"])
        assert "mail:read" in result
        assert "mail:write" in result

    def test_openid_passes_through(self):
        from app.mcp.auth import _normalize_scopes
        assert _normalize_scopes(["openid"]) == ["openid"]

    def test_mixed_scopes(self):
        from app.mcp.auth import _normalize_scopes
        result = _normalize_scopes(["mail.read", "mail.write", "openid", "calendar"])
        assert "mail:read" in result
        assert "mail:write" in result
        assert "openid" in result
        assert "calendar:read" in result
        assert "calendar:write" in result

    def test_deduplication(self):
        from app.mcp.auth import _normalize_scopes
        result = _normalize_scopes(["mail.read", "mail.read"])
        assert result == ["mail:read"]

    def test_full_chatgpt_scope_grants_all(self, app, authed_client, _clean_db):
        client, user_id, account_id = authed_client
        reg_data, _ = _register_client(client)
        client_id = reg_data["client_id"]
        redirect_uri = "https://chatgpt.com/connector/oauth/test123"
        verifier, challenge = _generate_pkce()
        resource = "https://example.com"

        chatgpt_scope = (
            "mail.read mail.write mail contacts.read contacts.write contacts"
            " calendar.read calendar.write calendar docs.read docs.write docs openid"
        )

        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        resp = client.post("/oauth/authorize", data={
            "action": "approve",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": chatgpt_scope,
            "scopes": chatgpt_scope.split(),
            "resource": resource,
            "state": "test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "code=" in location
        code = location.split("code=")[1].split("&")[0]

        token_resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert token_resp.status_code == 200
        token_data = json.loads(token_resp.data)
        assert token_data["scope"] == chatgpt_scope

        from app.mcp.auth import _normalize_scopes
        normalized = _normalize_scopes(token_data["scope"].split())
        for module in ("mail", "contacts", "calendar", "docs"):
            assert f"{module}:read" in normalized
            assert f"{module}:write" in normalized
        assert "openid" in normalized
