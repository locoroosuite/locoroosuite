from __future__ import annotations

import pytest

from app.shared.db import db as _db
from app.shared.models.core import User, Domain, CustomerAccount
from app.shared.keys import clear_user_key
from app.api.token_service import create_api_token, generate_dek
from app.mcp.auth import (
    McpAuthError,
    resolve_context,
    get_account_id,
    get_dek,
    require_scope,
    set_current_token,
    get_current_token,
)


def _create_test_user(app, email="mcp@example.com", account_active=True):
    with app.app_context():
        user = User(email=email, role="customer", is_active=True)
        user.password_hash = "x"
        _db.session.add(user)
        _db.session.flush()

        domain_name = email.split("@", 1)[1] if "@" in email else "example.com"
        domain = Domain(
            name=domain_name,
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

        dek = generate_dek()
        account = CustomerAccount(
            customer_id=user.id,
            domain_id=domain.id,
            email_address=email,
            auth_type="password",
            username=email,
            cache_db_path="",
            api_enabled=True,
            dek_wrapped_cred=b"placeholder",
            is_active=account_active,
        )
        _db.session.add(account)
        _db.session.commit()

        return user.id, account.id, dek


class TestApiKeyAuth:
    def test_resolve_api_key_valid(self, app, _clean_db):
        user_id, account_id, dek = _create_test_user(app)
        with app.app_context():
            token_value, _ = create_api_token(
                user_id, dek, "test-token", ["mail:read", "mail:write"]
            )
        ctx = resolve_context(token_value, app)
        assert ctx["customer_id"] == user_id
        assert ctx["dek"] == dek
        assert ctx["token_type"] == "api_key"
        assert "mail:read" in ctx["scopes"]
        assert "mail:write" in ctx["scopes"]

    def test_resolve_api_key_invalid(self, app, _clean_db):
        with pytest.raises(McpAuthError) as exc_info:
            resolve_context("lr_totally_invalid_token_value", app)
        assert exc_info.value.code == "AUTH_INVALID"

    def test_resolve_api_key_expired(self, app, _clean_db):
        fake_token = "lr_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        with pytest.raises(McpAuthError) as exc_info:
            resolve_context(fake_token, app)
        assert exc_info.value.code == "AUTH_INVALID"
        assert "Invalid or expired" in exc_info.value.message


class TestScopeEnforcement:
    def test_require_scope_read_allowed(self):
        ctx = {"scopes": ["mail:read"]}
        require_scope(ctx, "mail", "read")

    def test_require_scope_read_denied(self):
        ctx = {"scopes": ["contacts:read"]}
        with pytest.raises(McpAuthError) as exc_info:
            require_scope(ctx, "mail", "read")
        assert exc_info.value.code == "SCOPE_DENIED"

    def test_require_scope_write_allowed(self):
        ctx = {"scopes": ["mail:write"]}
        require_scope(ctx, "mail", "write")

    def test_require_scope_write_denied_read_only(self):
        ctx = {"scopes": ["mail:read"]}
        with pytest.raises(McpAuthError) as exc_info:
            require_scope(ctx, "mail", "write")
        assert exc_info.value.code == "SCOPE_DENIED"


class TestAccountIdResolution:
    def test_get_account_id_explicit(self, app, _clean_db):
        user_id, account_id, _ = _create_test_user(app)
        ctx = {"customer_id": user_id}
        result = get_account_id(ctx, app, account_id=account_id)
        assert result == account_id

    def test_get_account_id_default(self, app, _clean_db):
        user_id, account_id, _ = _create_test_user(app)
        ctx = {"customer_id": user_id}
        result = get_account_id(ctx, app)
        assert result == account_id

    def test_get_account_id_not_found(self, app, _clean_db):
        user_id, _, _ = _create_test_user(app)
        ctx = {"customer_id": user_id}
        with pytest.raises(McpAuthError) as exc_info:
            get_account_id(ctx, app, account_id=99999)
        assert exc_info.value.code == "NOT_FOUND"

    def test_get_account_id_no_active_account(self, app, _clean_db):
        user_id, _, _ = _create_test_user(app, account_active=False)
        ctx = {"customer_id": user_id}
        with pytest.raises(McpAuthError) as exc_info:
            get_account_id(ctx, app)
        assert exc_info.value.code == "NO_ACCOUNT"


class TestDekResolution:
    def test_get_dek_from_api_key_context(self, app, _clean_db):
        ctx = {"customer_id": 1, "dek": "abcdef0123456789"}
        result = get_dek(ctx, app)
        assert result == "abcdef0123456789"

    def test_get_dek_from_jwt_context_raises(self, app, _clean_db):
        user_id, _, _ = _create_test_user(app)
        ctx = {"customer_id": user_id}
        with pytest.raises(McpAuthError) as exc_info:
            get_dek(ctx, app)
        assert exc_info.value.code == "NO_DEK"


class TestContextVar:
    def test_set_get_current_token(self):
        set_current_token("lr_test123")
        assert get_current_token() == "lr_test123"
        set_current_token("")

    def test_default_token_empty(self):
        set_current_token("")
        assert get_current_token() == ""
