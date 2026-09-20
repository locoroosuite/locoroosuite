"""Unit tests for MatrixClient._request error mapping (app/modules/chat/services/matrix.py)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.modules.chat.services.matrix import MatrixClient, MatrixError


def _mock_response(status_code, payload=None, json_fails=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"body"
    if json_fails:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = payload if payload is not None else {}
    return resp


@pytest.fixture()
def client():
    return MatrixClient("http://synapse:8008", access_token="syt_probe")


@patch("app.modules.chat.services.matrix.requests.request")
def test_503_introspect_maps_to_auth_backend_down(mock_request, client):
    mock_request.return_value = _mock_response(
        503, {"errcode": "M_UNKNOWN", "error": "Unable to introspect the access token"}
    )
    with pytest.raises(MatrixError) as exc_info:
        client.whoami()
    assert exc_info.value.code == "MATRIX_AUTH_BACKEND_DOWN"
    assert exc_info.value.status == 503
    assert "authentication backend is down" in exc_info.value.message
    assert "homeserver operator" in exc_info.value.message


@patch("app.modules.chat.services.matrix.requests.request")
def test_503_other_error_keeps_generic_mapping(mock_request, client):
    mock_request.return_value = _mock_response(
        503, {"errcode": "M_LIMIT_EXCEEDED", "error": "Too many requests"}
    )
    with pytest.raises(MatrixError) as exc_info:
        client.whoami()
    assert exc_info.value.code == "M_LIMIT_EXCEEDED"
    assert exc_info.value.status == 503


@patch("app.modules.chat.services.matrix.requests.request")
def test_503_non_json_body_keeps_generic_mapping(mock_request, client):
    mock_request.return_value = _mock_response(503, json_fails=True)
    with pytest.raises(MatrixError) as exc_info:
        client.whoami()
    assert exc_info.value.code == "M_UNKNOWN"
    assert exc_info.value.status == 503
    assert "Chat server error (HTTP 503)" in exc_info.value.message


@patch("app.modules.chat.services.matrix.requests.request")
def test_401_unknown_token_passthrough(mock_request, client):
    mock_request.return_value = _mock_response(
        401, {"errcode": "M_UNKNOWN_TOKEN", "error": "Unrecognised access token"}
    )
    with pytest.raises(MatrixError) as exc_info:
        client.whoami()
    assert exc_info.value.code == "M_UNKNOWN_TOKEN"
    assert exc_info.value.status == 401


@patch("app.modules.chat.services.matrix.requests.request")
def test_connection_error_maps_to_unreachable(mock_request, client):
    mock_request.side_effect = requests.ConnectionError("connection refused")
    with pytest.raises(MatrixError) as exc_info:
        client.whoami()
    assert exc_info.value.code == "MATRIX_UNREACHABLE"


@patch("app.modules.chat.services.matrix.requests.request")
def test_success_returns_payload(mock_request, client):
    mock_request.return_value = _mock_response(200, {"user_id": "@probe:server"})
    assert client.whoami() == {"user_id": "@probe:server"}
