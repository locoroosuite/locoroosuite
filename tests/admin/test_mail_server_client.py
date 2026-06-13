from unittest.mock import patch, MagicMock

import pytest
import requests as real_requests

from app.admin.services.mail_server.http_client import MailApiClient

PATCH_TARGET = "app.admin.services.mail_server.http_client.requests.request"


def test_add_user_success():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"status": "ok", "email": "a@b.com"}
    mock_resp.raise_for_status = MagicMock()

    with patch(PATCH_TARGET, return_value=mock_resp) as mock_req:
        result = client.add_user("a@b.com", "secret")

    mock_req.assert_called_once()
    call_args = mock_req.call_args
    assert call_args[0][0] == "POST"
    assert "/api/users" in call_args[0][1]
    assert call_args[1]["json"] == {"email": "a@b.com", "password": "secret"}
    assert result["email"] == "a@b.com"


def test_add_domain_success():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"status": "ok", "domain": "test.com"}
    mock_resp.raise_for_status = MagicMock()

    with patch(PATCH_TARGET, return_value=mock_resp):
        result = client.add_domain("test.com")

    assert result["domain"] == "test.com"


def test_remove_domain():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    mock_resp.raise_for_status = MagicMock()

    with patch(PATCH_TARGET, return_value=mock_resp) as mock_req:
        client.remove_domain("test.com")

    call_args = mock_req.call_args
    assert call_args[0][0] == "DELETE"


def test_set_password():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    mock_resp.raise_for_status = MagicMock()

    with patch(PATCH_TARGET, return_value=mock_resp) as mock_req:
        client.set_password("a@b.com", "newpass")

    call_args = mock_req.call_args
    assert call_args[0][0] == "PUT"
    assert "a@b.com" in call_args[0][1]


def test_check_user_exists():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch(PATCH_TARGET, return_value=mock_resp):
        assert client.check_user("a@b.com") is True


def test_check_user_not_found():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch(PATCH_TARGET, return_value=mock_resp):
        assert client.check_user("a@b.com") is False


def test_connection_error():
    client = MailApiClient("http://mail-api:8800", "test-key")

    with patch(PATCH_TARGET, side_effect=real_requests.ConnectionError("refused")):
        with pytest.raises(real_requests.ConnectionError):
            client.add_domain("test.com")


def test_is_available_true():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch(PATCH_TARGET, return_value=mock_resp):
        assert client.is_available() is True


def test_is_available_false():
    client = MailApiClient("http://mail-api:8800", "test-key")

    with patch(PATCH_TARGET, side_effect=real_requests.ConnectionError("down")):
        assert client.is_available() is False


def test_auth_header_sent():
    client = MailApiClient("http://mail-api:8800", "my-secret-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    mock_resp.raise_for_status = MagicMock()

    with patch(PATCH_TARGET, return_value=mock_resp) as mock_req:
        client.add_domain("test.com")

    call_kwargs = mock_req.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Bearer my-secret-key"


def test_list_users():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"email": "a@test.com"}, {"email": "b@test.com"}]}
    mock_resp.raise_for_status = MagicMock()

    with patch(PATCH_TARGET, return_value=mock_resp) as mock_req:
        result = client.list_users("test.com")

    call_args = mock_req.call_args
    assert call_args[1]["params"] == {"domain": "test.com"}
    assert len(result) == 2
    assert result[0]["email"] == "a@test.com"


def test_list_users_no_domain():
    client = MailApiClient("http://mail-api:8800", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": []}
    mock_resp.raise_for_status = MagicMock()

    with patch(PATCH_TARGET, return_value=mock_resp) as mock_req:
        result = client.list_users()

    call_args = mock_req.call_args
    assert call_args[1]["params"] == {}
    assert result == []
