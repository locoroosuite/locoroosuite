from unittest.mock import MagicMock, patch

import pytest

from app.modules.chat.services import cache_db, provisioning
from app.modules.chat.services.matrix import MatrixError
from app.shared.models.core import CustomerAccount, Domain

MAS_USER_ID = "01MASUSER00000000000000000A"


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
        domain.matrix_mas_url = "http://mas:8080"
        domain.matrix_mas_client_id = "01MAS00000000000000000000A"
        domain.matrix_mas_client_secret = "dev-mas-client-secret"
        db.session.commit()
        yield domain.id, account_id, user_id
        from app.modules.chat.services.cache import get_cache_path

        acc = db.session.get(CustomerAccount, account_id)
        assert acc is not None
        path = get_cache_path(acc)
        import os

        if os.path.exists(path):
            os.unlink(path)


def _mock_mas(existing: dict | None = None, create_conflict: bool = False) -> MagicMock:
    """Mock MasClient. Resource shape: {"id": <ULID>, "attributes": {...}}."""
    mas = MagicMock()
    existing_resource = (
        {"id": MAS_USER_ID, "attributes": {"username": "tester"}} if existing else None
    )
    mas.get_user_by_username.return_value = existing_resource
    if create_conflict:
        mas.create_user.side_effect = MatrixError("MAS_USER_EXISTS", "already exists", 409)
    else:
        mas.create_user.return_value = {"id": MAS_USER_ID}
    mas.set_password.return_value = {}
    mas.login.return_value = {
        "user_id": "@tester:locoroo.test",
        "access_token": "mct_tok",
        "device_id": "DEV1",
    }
    return mas


def _mock_matrix_client() -> MagicMock:
    client = MagicMock()
    client.whoami.return_value = {"user_id": "@tester:locoroo.test"}
    return client


def test_ensure_matrix_user_creates_and_backs_up(app, authed_client, domain_with_matrix):
    domain_id, account_id, _user_id = domain_with_matrix
    mas = _mock_mas()
    with patch.object(provisioning, "mas_client_for_domain", return_value=mas), app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        result = provisioning.ensure_matrix_user(account, domain)
        assert result == {"matrix_user_id": None, "created": True}
        assert account.chat_encrypted_secret is not None
        password = provisioning._password_from_row_backup(account, domain)
        assert password is not None and len(password) >= 16
    mas.create_user.assert_called_once()
    localpart = provisioning.normalize_localpart(account.email_address, account.username)
    mas.set_password.assert_called_once_with(MAS_USER_ID, password)
    mas.create_user.assert_called_once_with(
        localpart, displayname=account.email_address.split("@", 1)[0]
    )


def test_ensure_matrix_user_with_server_name_returns_matrix_id(
    app, authed_client, domain_with_matrix
):
    domain_id, account_id, _user_id = domain_with_matrix
    mas = _mock_mas()
    with patch.object(provisioning, "mas_client_for_domain", return_value=mas), app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        result = provisioning.ensure_matrix_user(account, domain, server_name="locoroo.test")
        expected = provisioning.normalize_localpart(account.email_address, account.username)
        assert result == {"matrix_user_id": f"@{expected}:locoroo.test", "created": True}


def test_ensure_matrix_user_takes_over_existing_identity(app, authed_client, domain_with_matrix):
    domain_id, account_id, _user_id = domain_with_matrix
    mas = _mock_mas(existing={"id": MAS_USER_ID, "attributes": {"username": "tester"}})
    with patch.object(provisioning, "mas_client_for_domain", return_value=mas), app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        result = provisioning.ensure_matrix_user(account, domain, server_name="locoroo.test")
        assert result["created"] is False
        # Takeover resets the password and stores the row backup.
        assert account.chat_encrypted_secret is not None
    mas.create_user.assert_not_called()
    mas.set_password.assert_called_once()


def test_ensure_matrix_user_conflict_resolved_by_lookup(app, authed_client, domain_with_matrix):
    domain_id, account_id, _user_id = domain_with_matrix
    mas = _mock_mas(create_conflict=True)
    mas.get_user_by_username.side_effect = [None, {"id": MAS_USER_ID}]
    with patch.object(provisioning, "mas_client_for_domain", return_value=mas), app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        result = provisioning.ensure_matrix_user(account, domain)
        assert result["created"] is False
    mas.set_password.assert_called_once()


def test_ensure_matrix_user_conflict_without_resolution_raises(
    app, authed_client, domain_with_matrix
):
    domain_id, account_id, _user_id = domain_with_matrix
    mas = _mock_mas(create_conflict=True)
    with patch.object(provisioning, "mas_client_for_domain", return_value=mas), app.app_context():
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, domain_id)
        assert account is not None and domain is not None
        with pytest.raises(MatrixError) as excinfo:
            provisioning.ensure_matrix_user(account, domain)
        assert excinfo.value.code == "MAS_USER_EXISTS"
    mas.set_password.assert_not_called()


def test_ensure_matrix_user_requires_mas_config(app, authed_client, domain_with_matrix):
    domain_id, account_id, _user_id = domain_with_matrix
    with app.app_context():
        from app.shared.db import db

        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        domain.matrix_mas_url = None
        db.session.commit()
        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        with pytest.raises(MatrixError) as excinfo:
            provisioning.ensure_matrix_user(account, domain)
        assert excinfo.value.code == "MATRIX_NOT_CONFIGURED"


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
            return_value={"matrix_user_id": None, "created": True},
        ) as mock_ensure,
        app.app_context(),
    ):
        from app.shared.db import db

        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        result = provisioning.best_effort_provision(account)
        assert result == {"matrix_user_id": None, "created": True}
    mock_ensure.assert_called_once()


def _fake_mas_factory(mas):
    """Stand-in for mas_client_for_domain: hands out the mock MAS client."""
    calls = []

    def factory(domain):
        calls.append(domain)
        return mas

    return factory, calls


def test_provision_new_account(app, authed_client, domain_with_matrix):
    domain_id, account_id, user_id = domain_with_matrix
    mas = _mock_mas()
    matrix_client = _mock_matrix_client()
    factory, mas_constructions = _fake_mas_factory(mas)
    with (
        patch.object(provisioning, "mas_client_for_domain", side_effect=factory),
        patch.object(provisioning, "MatrixClient", return_value=matrix_client),
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
            assert stored["access_token"] == "mct_tok"
            assert account.chat_encrypted_secret is not None
        finally:
            conn.close()
    mas.login.assert_called_once()
    # Login went through the MAS compat layer, not the homeserver.
    assert mas_constructions and mas_constructions[0] is domain


def test_provision_reuses_valid_token(app, authed_client, domain_with_matrix):
    domain_id, account_id, user_id = domain_with_matrix
    matrix_constructions = []

    def matrix_factory(base_url, access_token=None, user_id=None):
        client = _mock_matrix_client()
        client.whoami.return_value = {"user_id": user_id or "@tester:locoroo.test"}
        matrix_constructions.append(access_token)
        return client

    mas = _mock_mas()
    with (
        patch.object(provisioning, "mas_client_for_domain", return_value=mas),
        patch.object(provisioning, "MatrixClient", side_effect=matrix_factory),
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
    mas.login.assert_called_once()
    assert matrix_constructions[-1] == "mct_tok"


def test_provision_relogin_on_invalid_token(app, authed_client, domain_with_matrix):
    domain_id, account_id, user_id = domain_with_matrix
    mas = _mock_mas()
    mas.login.side_effect = [
        {  # initial provision
            "user_id": "@tester:locoroo.test",
            "access_token": "mct_first",
            "device_id": "DEV1",
        },
        {  # re-login after token invalidation
            "user_id": "@tester:locoroo.test",
            "access_token": "mct_new",
            "device_id": "DEV2",
        },
    ]

    def matrix_factory(base_url, access_token=None, user_id=None):
        client = _mock_matrix_client()
        if access_token == "mct_first":
            client.whoami.side_effect = MatrixError("M_UNKNOWN_TOKEN", "token expired", 401)
        return client

    with (
        patch.object(provisioning, "mas_client_for_domain", return_value=mas),
        patch.object(provisioning, "MatrixClient", side_effect=matrix_factory),
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
        assert creds2["access_token"] == "mct_new"
    assert mas.login.call_count == 2
