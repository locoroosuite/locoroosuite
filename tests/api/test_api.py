import json
import pytest
from unittest.mock import patch, MagicMock


def _create_token(app, customer_id, dek_hex="a" * 64, name="test-token", scopes=None):
    from app.api.token_service import create_api_token
    if scopes is None:
        scopes = ["mail:read", "mail:write", "contacts:read", "contacts:write",
                  "calendar:read", "calendar:write", "docs:read", "docs:write"]
    return create_api_token(customer_id, dek_hex, name, scopes)


def _make_auth_header(token_value):
    return {"Authorization": f"Bearer {token_value}"}


class TestApiTokenAuth:
    def test_missing_auth_header(self, app, api_customer):
        client, user_id, account_id = api_customer
        resp = client.get("/api/v1/accounts")
        data = json.loads(resp.data)
        assert resp.status_code == 401
        assert data["error"]["code"] == "AUTH_MISSING"

    def test_invalid_token_format(self, app, api_customer):
        client, user_id, account_id = api_customer
        resp = client.get("/api/v1/accounts", headers={"Authorization": "Bearer bad_token"})
        data = json.loads(resp.data)
        assert resp.status_code == 401
        assert data["error"]["code"] == "AUTH_INVALID"

    def test_invalid_token_value(self, app, api_customer):
        client, user_id, account_id = api_customer
        resp = client.get("/api/v1/accounts", headers={"Authorization": "Bearer lr_invalidtoken123"})
        data = json.loads(resp.data)
        assert resp.status_code == 401
        assert data["error"]["code"] == "AUTH_INVALID"

    def test_valid_token(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, _ = _create_token(app, user_id)
        resp = client.get("/api/v1/accounts", headers=_make_auth_header(token_value))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "data" in data
        assert len(data["data"]) == 1
        assert data["data"][0]["email"] == "api@example.com"


class TestApiTokenScopeEnforcement:
    def test_read_scope_allows_read(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, _ = _create_token(app, user_id, scopes=["mail:read"])
        resp = client.get("/api/v1/accounts", headers=_make_auth_header(token_value))
        assert resp.status_code == 200

    def test_missing_scope_denied(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, _ = _create_token(app, user_id, scopes=["contacts:read"])
        resp = client.get("/api/v1/mail/folders?account_id={}".format(account_id),
                         headers=_make_auth_header(token_value))
        assert resp.status_code == 403
        data = json.loads(resp.data)
        assert data["error"]["code"] == "SCOPE_DENIED"

    def test_write_scope_denied_for_read_only(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, _ = _create_token(app, user_id, scopes=["mail:read"])
        resp = client.patch(
            f"/api/v1/mail/messages/1?account_id={account_id}",
            json={"flags": {"read": True}},
            headers=_make_auth_header(token_value),
        )
        assert resp.status_code == 403


class TestAccountsEndpoint:
    def test_list_accounts(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, _ = _create_token(app, user_id)
        resp = client.get("/api/v1/accounts", headers=_make_auth_header(token_value))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == account_id


class TestTokenManagement:
    def test_list_tokens(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, _ = _create_token(app, user_id, name="my-token")
        resp = client.get("/api/v1/tokens", headers=_make_auth_header(token_value))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) >= 1
        assert data["data"][0]["name"] == "my-token"

    def test_revoke_token(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, token_obj = _create_token(app, user_id, name="to-revoke")
            token_id = token_obj.id
        resp = client.delete(f"/api/v1/tokens/{token_id}",
                            headers=_make_auth_header(token_value))
        assert resp.status_code == 204

    def test_revoke_nonexistent_token(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, _ = _create_token(app, user_id)
        resp = client.delete("/api/v1/tokens/9999",
                            headers=_make_auth_header(token_value))
        assert resp.status_code == 404


class TestTokenService:
    def test_generate_token_format(self):
        from app.api.token_service import generate_token
        value, hash_val = generate_token()
        assert value.startswith("lr_")
        assert len(hash_val) == 64

    def test_wrap_unwrap_roundtrip(self):
        from app.api.token_service import wrap_dek_with_token, unwrap_dek_from_token
        dek = "a" * 64
        raw = b"test-raw-token-bytes"
        wrapped = wrap_dek_with_token(dek, raw)
        unwrapped = unwrap_dek_from_token(wrapped, raw)
        assert unwrapped == dek

    def test_create_and_authenticate(self, app, api_customer):
        client, user_id, account_id = api_customer
        with app.app_context():
            token_value, token_obj = _create_token(app, user_id, name="auth-test")
            from app.api.token_service import authenticate_token
            token, ctx = authenticate_token(token_value)
            assert token is not None
            assert ctx["customer_id"] == user_id
            assert "mail:read" in ctx["scopes"]

    def test_authenticate_invalid_token(self, app, api_customer):
        with app.app_context():
            from app.api.token_service import authenticate_token
            token, ctx = authenticate_token("lr_nonexistent")
            assert token is None

    def test_dek_credential_wrap_unwrap(self):
        from app.api.token_service import wrap_dek_with_credential, unwrap_dek_from_credential
        dek = "b" * 64
        cred_key = "c" * 64
        wrapped = wrap_dek_with_credential(dek, cred_key)
        unwrapped = unwrap_dek_from_credential(wrapped, cred_key)
        assert unwrapped == dek


class TestApiSettingsUI:
    def test_api_settings_page_requires_login(self, app, client):
        resp = client.get("/app/mail/settings/api")
        assert resp.status_code == 302

    def test_api_settings_page_shows_when_authed(self, app, api_customer):
        client, user_id, account_id = api_customer
        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id
        resp = client.get("/app/mail/settings/api")
        assert resp.status_code == 200
        assert b"API Access" in resp.data

    def test_enable_api_access(self, app, api_customer):
        client, user_id, account_id = api_customer
        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        from app.shared.models.core import CustomerAccount
        with app.app_context():
            account = CustomerAccount.query.filter_by(id=account_id).first()
            assert account is not None

        resp = client.post("/app/mail/settings/api/enable", data={"password": "test"})
        assert resp.status_code == 302

    def _setup_api_enabled(self, app, client, user_id, account_id):
        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        dek_hex = "a" * 64
        from app.shared.keys import set_user_key
        set_user_key(user_id, dek_hex)

        from app.shared.models.core import CustomerAccount
        from app.api.token_service import wrap_dek_with_credential
        with app.app_context():
            account = CustomerAccount.query.filter_by(id=account_id).first()
            account.api_enabled = True
            account.dek_wrapped_cred = wrap_dek_with_credential(dek_hex, "0" * 64)
            from app.shared.db import db
            db.session.commit()

        return dek_hex

    def test_create_token_via_settings(self, app, api_customer):
        client, user_id, account_id = api_customer
        dek_hex = self._setup_api_enabled(app, client, user_id, account_id)

        resp = client.post("/app/mail/settings/api/tokens/create", data={
            "token_name": "Test Token",
            "scope_mail_read": "on",
            "scope_mail_write": "on",
        })
        assert resp.status_code == 200
        assert b"lr_" in resp.data
        assert b"Token created" in resp.data

        from app.shared.models.core import ApiToken
        with app.app_context():
            token = ApiToken.query.filter_by(customer_id=user_id).first()
            assert token is not None
            assert token.name == "Test Token"

            import re
            match = re.search(r"lr_[A-Za-z0-9_-]+", resp.data.decode())
            assert match
            from app.api.token_service import unwrap_dek_from_token
            unwrapped = unwrap_dek_from_token(token.wrapped_dek, match.group(0).encode())
            assert unwrapped == dek_hex

    def test_create_token_missing_name_flashes_error(self, app, api_customer):
        client, user_id, account_id = api_customer
        self._setup_api_enabled(app, client, user_id, account_id)

        resp = client.post("/app/mail/settings/api/tokens/create", data={
            "token_name": "",
            "scope_mail_read": "on",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Token name is required" in resp.data

        from app.shared.models.core import ApiToken
        with app.app_context():
            assert ApiToken.query.filter_by(customer_id=user_id).count() == 0

    def test_create_token_no_scopes_flashes_error(self, app, api_customer):
        client, user_id, account_id = api_customer
        self._setup_api_enabled(app, client, user_id, account_id)

        resp = client.post("/app/mail/settings/api/tokens/create", data={
            "token_name": "No Scopes",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"At least one scope must be selected" in resp.data

    def test_create_token_no_session_key_returns_401(self, app, api_customer):
        client, user_id, account_id = api_customer
        self._setup_api_enabled(app, client, user_id, account_id)

        from app.shared.keys import clear_user_key
        clear_user_key(user_id)

        resp = client.post("/app/mail/settings/api/tokens/create", data={
            "token_name": "Ghost Token",
            "scope_mail_read": "on",
        })
        assert resp.status_code == 401

    def test_create_token_api_not_enabled_flashes_error(self, app, api_customer):
        client, user_id, account_id = api_customer
        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id

        from app.shared.keys import set_user_key
        set_user_key(user_id, "0" * 64)

        from app.shared.models.core import CustomerAccount
        with app.app_context():
            account = CustomerAccount.query.filter_by(id=account_id).first()
            account.api_enabled = False
            from app.shared.db import db
            db.session.commit()

        resp = client.post("/app/mail/settings/api/tokens/create", data={
            "token_name": "Disabled API",
            "scope_mail_read": "on",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"API access is not enabled" in resp.data

    def test_revoke_token_via_settings(self, app, api_customer):
        client, user_id, account_id = api_customer
        self._setup_api_enabled(app, client, user_id, account_id)

        with app.app_context():
            token_value, token_obj = _create_token(app, user_id, name="revoke-me")
            token_id = token_obj.id

        resp = client.post(f"/app/mail/settings/api/tokens/{token_id}/revoke")
        assert resp.status_code == 302

        with app.app_context():
            from app.shared.models.core import ApiToken
            assert ApiToken.query.filter_by(id=token_id).first() is None


class TestApiSettingsLink:
    def test_settings_page_has_api_link(self, app, api_customer):
        client, user_id, account_id = api_customer
        with client.session_transaction() as sess:
            sess["role"] = "customer"
            sess["user_id"] = user_id
            sess["active_account_id"] = account_id
        resp = client.get("/app/mail/settings")
        assert resp.status_code == 200
        assert b"API Access" in resp.data
