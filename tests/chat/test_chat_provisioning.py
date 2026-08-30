from unittest.mock import MagicMock, patch

import pytest

from app.modules.chat.services import cache_db, provisioning
from app.modules.chat.services.matrix import MatrixError
from app.shared.models.core import CustomerAccount, Domain


@pytest.fixture()
def domain_with_matrix(app, authed_client):
    from app.shared.db import db

    _client, user_id, account_id = authed_client
    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        domain = db.session.get(Domain, account.domain_id)
        assert domain is not None
        domain.matrix_host = "synapse"
        domain.matrix_port = 8008
        domain.matrix_use_tls = False
        domain.matrix_shared_secret = "dev-matrix-shared-secret"
        db.session.commit()
        yield domain.id, account_id, user_id
        from app.modules.chat.services.cache import get_cache_path

        acc = db.session.get(CustomerAccount, account_id)
        assert acc is not None
        path = get_cache_path(acc)
        import os

        if os.path.exists(path):
            os.unlink(path)


def _mock_register_client():
    client = MagicMock()
    client.admin_register.return_value = {
        "user_id": "@tester:locoroo.test",
        "access_token": "syt_tok",
        "device_id": "DEV1",
    }
    client.login.return_value = {
        "user_id": "@tester:locoroo.test",
        "access_token": "syt_tok",
        "device_id": "DEV1",
    }
    client.whoami.return_value = {"user_id": "@tester:locoroo.test"}
    client.set_displayname.return_value = {}
    return client


def test_ensure_matrix_user_creates_and_backs_up(app, authed_client, domain_with_matrix):
    domain_id, account_id, _user_id = domain_with_matrix
    mock = _mock_register_client()
    with patch.object(provisioning, "MatrixClient", return_value=mock), app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        result = provisioning.ensure_matrix_user(account, domain)
        assert result == {"matrix_user_id": "@tester:locoroo.test", "created": True}
        assert account.chat_encrypted_secret is not None
        password = provisioning._password_from_row_backup(account, domain)
        assert password is not None and len(password) >= 16
    mock.admin_register.assert_called_once()
    mock.set_displayname.assert_called_once()


def test_ensure_matrix_user_existing(app, authed_client, domain_with_matrix):
    domain_id, account_id, _user_id = domain_with_matrix
    mock = _mock_register_client()
    mock.admin_register.side_effect = MatrixError("M_USER_IN_USE", "exists", 400)
    with patch.object(provisioning, "MatrixClient", return_value=mock), app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        result = provisioning.ensure_matrix_user(account, domain, server_name="locoroo.test")
        expected_localpart = provisioning.normalize_localpart(
            account.email_address, account.username
        )
        assert result == {
            "matrix_user_id": f"@{expected_localpart}:locoroo.test",
            "created": False,
        }
        assert account.chat_encrypted_secret is None


def test_best_effort_skips_unconfigured(app, authed_client):
    _client, _user_id, account_id = authed_client
    with patch.object(provisioning, "ensure_matrix_user") as mock_ensure, app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        assert provisioning.best_effort_provision(account) is None
    mock_ensure.assert_not_called()


def test_best_effort_provisions(app, authed_client, domain_with_matrix):
    _domain_id, account_id, _user_id = domain_with_matrix
    with (
        patch.object(
            provisioning,
            "ensure_matrix_user",
            return_value={"matrix_user_id": "@tester:locoroo.test", "created": True},
        ) as mock_ensure,
        app.app_context(),
    ):
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        result = provisioning.best_effort_provision(account)
        assert result == {"matrix_user_id": "@tester:locoroo.test", "created": True}
    mock_ensure.assert_called_once()


def test_provision_new_account(app, authed_client, domain_with_matrix):
    domain_id, account_id, user_id = domain_with_matrix
    mock_client = _mock_register_client()
    with (
        patch.object(provisioning, "MatrixClient", return_value=mock_client),
        app.app_context(),
    ):
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        conn, _client, creds = provisioning.ensure_chat_client(account, domain, user_id)
        try:
            assert creds["matrix_user_id"] == "@tester:locoroo.test"
            stored = cache_db.get_credentials(conn)
            assert stored is not None
            assert stored["access_token"] == "syt_tok"
            assert account.chat_encrypted_secret is not None
        finally:
            conn.close()
    mock_client.admin_register.assert_called_once()
    mock_client.login.assert_called_once()


def test_provision_reuses_valid_token(app, authed_client, domain_with_matrix):
    domain_id, account_id, user_id = domain_with_matrix
    constructions = []

    def client_factory(base_url, access_token=None, user_id=None):
        client = _mock_register_client()
        client.whoami.return_value = {"user_id": user_id or "@tester:locoroo.test"}
        constructions.append(access_token)
        return client

    with (
        patch.object(provisioning, "MatrixClient", side_effect=client_factory),
        app.test_request_context(),
    ):
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        conn1, _c1, creds1 = provisioning.ensure_chat_client(account, domain, user_id)
        conn1.close()
        conn2, _c2, creds2 = provisioning.ensure_chat_client(account, domain, user_id)
        conn2.close()
        assert creds1["access_token"] == creds2["access_token"]
    # provision (register + login + returned client) + whoami-reuse on second call
    assert len(constructions) == 4
    assert constructions[-1] == "syt_tok"


def test_provision_relogin_on_invalid_token(app, authed_client, domain_with_matrix):
    domain_id, account_id, user_id = domain_with_matrix

    def client_factory(base_url, access_token=None, user_id=None):
        client = _mock_register_client()
        if access_token == "syt_tok":
            client.whoami.side_effect = MatrixError("M_UNKNOWN_TOKEN", "token expired", 401)
        client.login.return_value = {
            "user_id": "@tester:locoroo.test",
            "access_token": "syt_new",
            "device_id": "DEV2",
        }
        return client

    with (
        patch.object(provisioning, "MatrixClient", side_effect=client_factory),
        app.test_request_context(),
    ):
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        conn, _c, _creds = provisioning.ensure_chat_client(account, domain, user_id)
        conn.close()
        conn2, _c2, creds2 = provisioning.ensure_chat_client(account, domain, user_id)
        conn2.close()
        assert creds2["access_token"] == "syt_new"
