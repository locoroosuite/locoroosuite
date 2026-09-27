"""Unit tests for MasClient (app/modules/chat/services/mas.py).

Covers the admin-token client-credentials flow and the 404 semantics that
differ per endpoint: by-username 404 means "user not found", while a 404 on
the users collection or set-password means the adminapi resource is absent.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.chat.services.mas import MasClient
from app.modules.chat.services.matrix import MatrixError

USER_RESOURCE = {"data": {"type": "user", "id": "01MASUSER00000000000000000A"}}


def _mock_response(status_code, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"body"
    resp.json.return_value = payload if payload is not None else {}
    return resp


@pytest.fixture()
def mas():
    return MasClient("http://mas:8080", "01MAS00000000000000000000A", "dev-mas-client-secret")


def _mock_token(mock_post):
    mock_post.return_value = _mock_response(
        200, {"access_token": "mat_admin", "token_type": "Bearer", "expires_in": 300}
    )


@patch("app.modules.chat.services.mas.requests.post")
def test_fetch_admin_token_uses_client_credentials(mock_post, mas):
    _mock_token(mock_post)
    assert mas.fetch_admin_token() == "mat_admin"
    args, kwargs = mock_post.call_args
    assert args[0] == "http://mas:8080/oauth2/token"
    assert kwargs["auth"] == ("01MAS00000000000000000000A", "dev-mas-client-secret")
    assert kwargs["data"] == {
        "grant_type": "client_credentials",
        "scope": "urn:mas:admin",
    }


@patch("app.modules.chat.services.mas.requests.post")
def test_fetch_admin_token_rejected_credentials(mock_post, mas):
    mock_post.return_value = _mock_response(401, {"error": "invalid_client"})
    with pytest.raises(MatrixError) as exc_info:
        mas.fetch_admin_token()
    assert exc_info.value.code == "MATRIX_MAS_AUTH_FAILED"
    assert "admin_clients" in exc_info.value.message


@patch("app.modules.chat.services.mas.requests.post")
def test_fetch_admin_token_unreachable(mock_post, mas):
    import requests as requests_lib

    mock_post.side_effect = requests_lib.RequestException("boom")
    with pytest.raises(MatrixError) as exc_info:
        mas.fetch_admin_token()
    assert exc_info.value.code == "MATRIX_MAS_UNREACHABLE"


@patch("app.modules.chat.services.mas.requests.request")
@patch("app.modules.chat.services.mas.requests.post")
def test_by_username_404_means_not_found(mock_post, mock_request, mas):
    _mock_token(mock_post)
    mock_request.return_value = _mock_response(
        404, {"errors": [{"title": 'User with username "x" not found'}]}
    )
    assert mas.get_user_by_username("x") is None


@patch("app.modules.chat.services.mas.requests.request")
@patch("app.modules.chat.services.mas.requests.post")
def test_create_user_maps_conflict(mock_post, mock_request, mas):
    _mock_token(mock_post)
    mock_request.return_value = _mock_response(409, {"errors": [{"title": "already taken"}]})
    with pytest.raises(MatrixError) as exc_info:
        mas.create_user("x")
    assert exc_info.value.code == "MAS_USER_EXISTS"


@patch("app.modules.chat.services.mas.requests.request")
@patch("app.modules.chat.services.mas.requests.post")
def test_create_user_404_means_admin_api_disabled(mock_post, mock_request, mas):
    _mock_token(mock_post)
    mock_request.return_value = _mock_response(404, {})
    with pytest.raises(MatrixError) as exc_info:
        mas.create_user("x")
    assert exc_info.value.code == "MATRIX_MAS_ADMIN_API_DISABLED"
    assert "adminapi" in exc_info.value.message


@patch("app.modules.chat.services.mas.requests.request")
@patch("app.modules.chat.services.mas.requests.post")
def test_set_password_404_means_admin_api_disabled(mock_post, mock_request, mas):
    _mock_token(mock_post)
    mock_request.return_value = _mock_response(404, {})
    with pytest.raises(MatrixError) as exc_info:
        mas.set_password("01MASUSER00000000000000000A", "pw")
    assert exc_info.value.code == "MATRIX_MAS_ADMIN_API_DISABLED"


@patch("app.modules.chat.services.mas.requests.request")
@patch("app.modules.chat.services.mas.requests.post")
def test_create_user_happy_path(mock_post, mock_request, mas):
    _mock_token(mock_post)
    mock_request.return_value = _mock_response(201, USER_RESOURCE)
    assert mas.create_user("x", displayname="X") == USER_RESOURCE["data"]
    args, kwargs = mock_request.call_args
    assert args[0] == "POST"
    assert args[1] == "http://mas:8080/api/admin/v1/users"
    assert kwargs["json"] == {"username": "x", "displayname": "X"}
    assert kwargs["headers"]["Authorization"] == "Bearer mat_admin"


@patch("app.modules.chat.services.mas.requests.request")
@patch("app.modules.chat.services.mas.requests.post")
def test_admin_request_refetches_token_on_401(mock_post, mock_request, mas):
    expired = _mock_response(
        200, {"access_token": "mat_expired", "token_type": "Bearer", "expires_in": 300}
    )
    fresh = _mock_response(
        200, {"access_token": "mat_fresh", "token_type": "Bearer", "expires_in": 300}
    )
    unauthorized = _mock_response(401, {"errors": [{"title": "token expired"}]})
    ok = _mock_response(200, USER_RESOURCE)
    mock_post.side_effect = [expired, fresh]
    mock_request.side_effect = [unauthorized, ok]
    assert mas.get_user_by_username("x") == USER_RESOURCE["data"]
    assert mock_post.call_count == 2


@patch("app.modules.chat.services.mas.requests.request")
def test_compat_login_passthrough(mock_request, mas):
    mock_request.return_value = _mock_response(
        200, {"user_id": "@x:server", "access_token": "mct_t", "device_id": "D1"}
    )
    assert mas.login("x", "pw")["access_token"] == "mct_t"
    args, kwargs = mock_request.call_args
    assert args == ("POST", "http://mas:8080/_matrix/client/v3/login")
    assert kwargs["json"]["type"] == "m.login.password"
    assert kwargs["json"]["password"] == "pw"


def test_mas_client_for_domain_requires_all_fields():
    from types import SimpleNamespace

    from app.modules.chat.services.mas import mas_client_for_domain

    domain = SimpleNamespace(
        matrix_host="synapse",
        matrix_port=8008,
        matrix_use_tls=False,
        matrix_mas_url=None,
        matrix_mas_client_id="01MAS00000000000000000000A",
        matrix_mas_client_secret="dev-mas-client-secret",
    )
    with pytest.raises(MatrixError) as exc_info:
        mas_client_for_domain(domain)
    assert exc_info.value.code == "MATRIX_NOT_CONFIGURED"

    domain.matrix_mas_url = "http://mas:8080"
    client = mas_client_for_domain(domain)
    assert client.base_url == "http://mas:8080"
