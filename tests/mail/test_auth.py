import pytest
from unittest.mock import patch, MagicMock

from app.shared.db import db
from app.shared.models.core import Domain, User, DocShare


def test_login_page_renders(client, app):
    _setup_domain(app)
    resp = client.get("/app/login")
    assert resp.status_code == 200
    text = resp.data.decode().lower()
    assert "login" in text or "email" in text


def test_login_post_success(client, app):
    with app.app_context():
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
        db.session.add(domain)
        db.session.commit()

    mock_client = MagicMock()
    with (
        patch("app.modules.mail.controllers.auth.connect_imap", return_value=mock_client),
        patch("app.modules.mail.controllers.auth.login_imap"),
        patch("app.modules.mail.controllers.auth.safe_logout"),
        patch("app.modules.mail.controllers.auth.derive_key", return_value="0" * 64),
        patch("app.modules.mail.controllers.auth.encrypt_with_key", return_value=b"x"),
        patch("app.modules.mail.controllers.auth.build_cache_path", return_value="/tmp/test.db"),
    ):
        resp = client.post("/app/login", data={"email": "user@example.com", "password": "secret"}, follow_redirects=False)

    assert resp.status_code == 302


def test_login_post_domain_disabled(client):
    resp = client.post("/app/login", data={"email": "user@nope.xyz", "password": "secret"})
    assert resp.status_code == 200
    assert "Domain not enabled" in resp.data.decode()


def _setup_domain(app):
    with app.app_context():
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
        db.session.add(domain)
        db.session.commit()


def _mock_imap_patches():
    mock_client = MagicMock()
    return [
        patch("app.modules.mail.controllers.auth.connect_imap", return_value=mock_client),
        patch("app.modules.mail.controllers.auth.login_imap"),
        patch("app.modules.mail.controllers.auth.safe_logout"),
        patch("app.modules.mail.controllers.auth.derive_key", return_value="0" * 64),
        patch("app.modules.mail.controllers.auth.encrypt_with_key", return_value=b"x"),
        patch("app.modules.mail.controllers.auth.build_cache_path", return_value="/tmp/test.db"),
    ]


def test_login_get_redirects_to_setup_when_no_domains(client):
    resp = client.get("/app/login", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/setup" in resp.headers["Location"]


class TestLoginNextParameter:
    def test_login_get_with_next_renders_hidden_field(self, client, app):
        _setup_domain(app)
        resp = client.get("/app/login?next=/oauth/authorize%3Fclient_id%3Dabc")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="next"' in html
        assert 'value="/oauth/authorize?client_id=abc"' in html

    def test_login_get_without_next_no_hidden_field(self, client, app):
        _setup_domain(app)
        resp = client.get("/app/login")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="next"' not in html

    def test_login_post_with_next_redirects_to_next(self, client, app):
        _setup_domain(app)
        next_url = "/oauth/authorize?client_id=abc&redirect_uri=https%3A//example.com"
        with app.app_context():
            app.config["SERVER_NAME"] = "localhost"
            patches = _mock_imap_patches()
            for p in patches:
                p.start()
            try:
                resp = client.post(
                    "/app/login",
                    data={"email": "user@example.com", "password": "secret", "next": next_url},
                    follow_redirects=False,
                )
            finally:
                for p in patches:
                    p.stop()
                app.config["SERVER_NAME"] = ""

        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert location.startswith("/oauth/authorize")

    def test_login_post_without_next_redirects_to_inbox(self, client, app):
        _setup_domain(app)
        patches = _mock_imap_patches()
        for p in patches:
            p.start()
        try:
            resp = client.post(
                "/app/login",
                data={"email": "user@example.com", "password": "secret"},
                follow_redirects=False,
            )
        finally:
            for p in patches:
                p.stop()

        assert resp.status_code == 302
        assert "/app/mail/folder/" in resp.headers["Location"]

    def test_login_post_with_external_next_ignores_it(self, client, app):
        _setup_domain(app)
        with app.app_context():
            app.config["SERVER_NAME"] = "localhost"
            patches = _mock_imap_patches()
            for p in patches:
                p.start()
            try:
                resp = client.post(
                    "/app/login",
                    data={"email": "user@example.com", "password": "secret", "next": "https://evil.com/phish"},
                    follow_redirects=False,
                )
            finally:
                for p in patches:
                    p.stop()
                app.config["SERVER_NAME"] = ""

        assert resp.status_code == 302
        assert "/app/mail/folder/" in resp.headers["Location"]

    def test_login_post_with_scheme_relative_next_ignores_it(self, client, app):
        _setup_domain(app)
        with app.app_context():
            app.config["SERVER_NAME"] = "localhost"
            patches = _mock_imap_patches()
            for p in patches:
                p.start()
            try:
                resp = client.post(
                    "/app/login",
                    data={"email": "user@example.com", "password": "secret", "next": "//evil.com/phish"},
                    follow_redirects=False,
                )
            finally:
                for p in patches:
                    p.stop()
                app.config["SERVER_NAME"] = ""

        assert resp.status_code == 302
        assert "/app/mail/folder/" in resp.headers["Location"]

    def test_login_post_error_preserves_next_in_form(self, client):
        resp = client.post(
            "/app/login",
            data={"email": "user@nope.xyz", "password": "secret", "next": "/oauth/authorize?client_id=abc"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Domain not enabled" in html
        assert 'name="next"' in html
        assert "/oauth/authorize?client_id=abc" in html

    def test_oauth_authorize_redirects_to_login_with_next(self, client, app, _clean_db):
        with app.app_context():
            app.config["SERVER_NAME"] = "localhost"
            resp = client.get(
                "/oauth/authorize",
                query_string={
                    "client_id": "test-client",
                    "redirect_uri": "https://chatgpt.com/connector/oauth/test",
                    "response_type": "code",
                    "code_challenge": "abc123",
                    "code_challenge_method": "S256",
                    "resource": "https://example.com",
                },
                follow_redirects=False,
            )
            app.config["SERVER_NAME"] = ""

        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/app/login" in location
        assert "next=" in location
        assert "/oauth/authorize" in location

    def test_login_post_with_valid_server_name_absolute_next(self, client, app):
        _setup_domain(app)
        with app.app_context():
            app.config["SERVER_NAME"] = "app.example.com"
            patches = _mock_imap_patches()
            for p in patches:
                p.start()
            try:
                resp = client.post(
                    "/app/login",
                    data={
                        "email": "user@example.com",
                        "password": "secret",
                        "next": "https://app.example.com/oauth/authorize?client_id=abc",
                    },
                    follow_redirects=False,
                )
            finally:
                for p in patches:
                    p.stop()
                app.config["SERVER_NAME"] = ""

        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/oauth/authorize" in location

    def test_login_post_with_wrong_host_absolute_next_ignores_it(self, client, app):
        _setup_domain(app)
        with app.app_context():
            app.config["SERVER_NAME"] = "app.example.com"
            patches = _mock_imap_patches()
            for p in patches:
                p.start()
            try:
                resp = client.post(
                    "/app/login",
                    data={
                        "email": "user@example.com",
                        "password": "secret",
                        "next": "https://evil.com/oauth/authorize",
                    },
                    follow_redirects=False,
                )
            finally:
                for p in patches:
                    p.stop()
                app.config["SERVER_NAME"] = ""

        assert resp.status_code == 302
        assert "/app/mail/folder/" in resp.headers["Location"]


def test_logout(authed_client):
    client, user_id, account_id = authed_client
    resp = client.get("/app/logout")
    assert resp.status_code == 302
    assert "/app/login" in resp.headers["Location"]


def test_root_redirects_to_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/app/login" in resp.headers["Location"]


def test_auth_check_unauthenticated(client):
    resp = client.get("/app/auth/check")
    assert resp.status_code == 401
    assert resp.data == b""


def test_auth_check_customer(authed_client):
    client, user_id, account_id = authed_client
    resp = client.get("/app/auth/check")
    assert resp.status_code == 200
    assert resp.data == b""


def test_auth_check_admin_rejected(app, client, _clean_db):
    with app.app_context():
        admin = User(email="admin@example.com", role="admin", is_active=True)
        admin.password_hash = "x"
        db.session.add(admin)
        db.session.flush()
        admin_id = admin.id
        db.session.commit()

    with client.session_transaction() as sess:
        sess["role"] = "admin"
        sess["user_id"] = admin_id

    resp = client.get("/app/auth/check")
    assert resp.status_code == 401


def test_auth_check_manager_rejected(app, client, _clean_db):
    with app.app_context():
        manager = User(email="manager@example.com", role="manager", is_active=True)
        manager.password_hash = "x"
        db.session.add(manager)
        db.session.flush()
        manager_id = manager.id
        db.session.commit()

    with client.session_transaction() as sess:
        sess["role"] = "manager"
        sess["user_id"] = manager_id

    resp = client.get("/app/auth/check")
    assert resp.status_code == 401


def test_auth_check_no_role(client, app, _clean_db):
    with app.app_context():
        user = User(email="norole@example.com", role="customer", is_active=True)
        user.password_hash = "x"
        db.session.add(user)
        db.session.flush()
        user_id = user.id
        db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = user_id

    resp = client.get("/app/auth/check")
    assert resp.status_code == 401


def test_auth_check_share_cookie_valid(client, app, _clean_db):
    with app.app_context():
        share = DocShare(
            doc_id="fakedoc",
            owner_user_id=1,
            owner_account_id=1,
            share_token="validsharecookie",
            permission="view",
            share_type="link",
            recipient_email="ext@gmail.com",
            doc_name="Test",
            doc_type="odt",
        )
        db.session.add(share)
        db.session.commit()

    client.set_cookie("share_access", "validsharecookie")
    resp = client.get("/app/auth/check")
    assert resp.status_code == 200
    assert resp.data == b""


def test_auth_check_share_cookie_revoked(client, app, _clean_db):
    from datetime import datetime, timezone

    with app.app_context():
        share = DocShare(
            doc_id="fakedoc",
            owner_user_id=1,
            owner_account_id=1,
            share_token="revokedcookie",
            permission="view",
            share_type="link",
            recipient_email="ext@gmail.com",
            doc_name="Test",
            doc_type="odt",
            revoked_at=datetime.now(timezone.utc),
        )
        db.session.add(share)
        db.session.commit()

    client.set_cookie("share_access", "revokedcookie")
    resp = client.get("/app/auth/check")
    assert resp.status_code == 401


def test_auth_check_share_cookie_invalid(client, app, _clean_db):
    client.set_cookie("share_access", "nonexistenttoken123")
    resp = client.get("/app/auth/check")
    assert resp.status_code == 401


def test_auth_check_session_takes_precedence_over_share_cookie(client, app, _clean_db):
    from app.shared.keys import set_user_key, clear_user_key

    with app.app_context():
        user = User(email="precedence@example.com", role="customer", is_active=True)
        user.password_hash = "x"
        db.session.add(user)
        db.session.flush()
        user_id = user.id
        db.session.commit()

    set_user_key(user_id, "0" * 64)

    with client.session_transaction() as sess:
        sess["role"] = "customer"
        sess["user_id"] = user_id

    client.set_cookie("share_access", "nonexistenttoken")
    resp = client.get("/app/auth/check")
    assert resp.status_code == 200

    clear_user_key(user_id)


def test_set_timezone_success(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post(
        "/app/api/set-timezone",
        json={"timezone": "Australia/Adelaide"},
    )
    assert resp.status_code == 200
    assert resp.json == {"ok": True}
    with client.session_transaction() as sess:
        assert sess["_browser_tz"] == "Australia/Adelaide"


def test_set_timezone_missing_field(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post("/app/api/set-timezone", json={})
    assert resp.status_code == 400


def test_set_timezone_invalid_timezone(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post(
        "/app/api/set-timezone",
        json={"timezone": "Invalid/Zone"},
    )
    assert resp.status_code == 400


def test_set_timezone_unauthenticated(client):
    resp = client.post(
        "/app/api/set-timezone",
        json={"timezone": "Australia/Adelaide"},
    )
    assert resp.status_code == 302


class TestLoginWithApiEnabled:
    def _setup_account_with_api(self, app, dek_hex="a" * 64, credential_key="0" * 64):
        from app.shared.models.core import CustomerAccount
        from app.api.token_service import wrap_dek_with_credential
        from app.shared.keys import get_user_key

        user_id = None
        account_id = None
        with app.app_context():
            user = User(email="apiuser@example.com", role="customer", is_active=True)
            user.password_hash = "x"
            db.session.add(user)
            db.session.flush()
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
            db.session.add(domain)
            db.session.flush()

            wrapped_dek = wrap_dek_with_credential(dek_hex, credential_key)
            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="apiuser@example.com",
                auth_type="password",
                username="apiuser@example.com",
                cache_db_path="",
                api_enabled=True,
                dek_wrapped_cred=wrapped_dek,
            )
            db.session.add(account)
            db.session.commit()
            account_id = account.id

        return user_id, account_id, dek_hex, credential_key

    def test_login_with_api_enabled_sets_dek_as_user_key(self, app, client):
        user_id, account_id, dek_hex, credential_key = self._setup_account_with_api(app)

        from app.shared.keys import get_user_key
        mock_client = MagicMock()
        with (
            patch("app.modules.mail.controllers.auth.connect_imap", return_value=mock_client),
            patch("app.modules.mail.controllers.auth.login_imap"),
            patch("app.modules.mail.controllers.auth.safe_logout"),
            patch("app.modules.mail.controllers.auth.derive_key", return_value=credential_key),
            patch("app.modules.mail.controllers.auth.build_cache_path", return_value="/tmp/test.db"),
        ):
            resp = client.post("/app/login", data={"email": "apiuser@example.com", "password": "secret"})

        assert resp.status_code == 302
        assert get_user_key(user_id) == dek_hex

        with app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.modules.mail.services.secrets import decrypt_with_key
            account = db.session.get(CustomerAccount, account_id)
            decrypted = decrypt_with_key(account.encrypted_secret, dek_hex)
            assert decrypted == b"secret" or decrypted == "secret"

        from app.shared.keys import clear_user_key
        clear_user_key(user_id)

    def test_login_without_api_enabled_uses_credential_key(self, app, client):
        from app.shared.models.core import CustomerAccount
        user_id = None
        account_id = None
        credential_key = "0" * 64
        with app.app_context():
            user = User(email="normal@example.com", role="customer", is_active=True)
            user.password_hash = "x"
            db.session.add(user)
            db.session.flush()
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
            db.session.add(domain)
            db.session.flush()

            account = CustomerAccount(
                customer_id=user.id,
                domain_id=domain.id,
                email_address="normal@example.com",
                auth_type="password",
                username="normal@example.com",
                cache_db_path="",
                api_enabled=False,
            )
            db.session.add(account)
            db.session.commit()
            account_id = account.id

        from app.shared.keys import get_user_key
        mock_client = MagicMock()
        with (
            patch("app.modules.mail.controllers.auth.connect_imap", return_value=mock_client),
            patch("app.modules.mail.controllers.auth.login_imap"),
            patch("app.modules.mail.controllers.auth.safe_logout"),
            patch("app.modules.mail.controllers.auth.derive_key", return_value=credential_key),
            patch("app.modules.mail.controllers.auth.build_cache_path", return_value="/tmp/test.db"),
        ):
            resp = client.post("/app/login", data={"email": "normal@example.com", "password": "secret"})

        assert resp.status_code == 302
        assert get_user_key(user_id) == credential_key

        from app.shared.keys import clear_user_key
        clear_user_key(user_id)

    def test_login_api_enabled_unwrap_fails_falls_back_to_credential_key(self, app, client):
        user_id, account_id, dek_hex, credential_key = self._setup_account_with_api(app)

        from app.shared.keys import get_user_key
        wrong_key = "b" * 64
        mock_client = MagicMock()
        with (
            patch("app.modules.mail.controllers.auth.connect_imap", return_value=mock_client),
            patch("app.modules.mail.controllers.auth.login_imap"),
            patch("app.modules.mail.controllers.auth.safe_logout"),
            patch("app.modules.mail.controllers.auth.derive_key", return_value=wrong_key),
            patch("app.modules.mail.controllers.auth.build_cache_path", return_value="/tmp/test.db"),
        ):
            resp = client.post("/app/login", data={"email": "apiuser@example.com", "password": "secret"})

        assert resp.status_code == 302
        assert get_user_key(user_id) == wrong_key

        from app.shared.keys import clear_user_key
        clear_user_key(user_id)
