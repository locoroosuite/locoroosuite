"""Unit tests for domain service health checks (app/admin/services/health_checks.py)."""

from types import SimpleNamespace
from unittest.mock import patch

from app.admin.services.health_checks import _check_matrix, check_domain_services
from app.modules.chat.services.matrix import MatrixError
from app.shared.models.core import Domain


def _matrix_domain(**overrides):
    values = {
        "matrix_host": "synapse",
        "matrix_port": 8008,
        "matrix_use_tls": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_matrix_not_configured():
    assert _check_matrix(_matrix_domain(matrix_host=None)) == "not_configured"


@patch("app.admin.services.health_checks._tcp_check", return_value=False)
def test_matrix_tcp_unreachable_is_misconfigured(mock_tcp):
    assert _check_matrix(_matrix_domain()) == "misconfigured"
    mock_tcp.assert_called_once_with("synapse", 8008)


@patch("app.modules.chat.services.matrix.MatrixClient")
@patch("app.admin.services.health_checks._tcp_check", return_value=True)
def test_matrix_auth_backend_down_classified_distinctly(mock_tcp, mock_client_cls):
    mock_client_cls.return_value.whoami.side_effect = MatrixError(
        "MATRIX_AUTH_BACKEND_DOWN",
        "The chat server's authentication backend is down; contact the homeserver operator.",
        503,
    )
    assert _check_matrix(_matrix_domain()) == "auth_backend_down"
    mock_client_cls.assert_called_once_with(
        "http://synapse:8008", access_token="locooro-health-probe"
    )
    mock_client_cls.return_value.whoami.assert_called_once_with(timeout=3)


@patch("app.modules.chat.services.matrix.MatrixClient")
@patch("app.admin.services.health_checks._tcp_check", return_value=True)
def test_matrix_http_unreachable_is_misconfigured(mock_tcp, mock_client_cls):
    mock_client_cls.return_value.whoami.side_effect = MatrixError(
        "MATRIX_UNREACHABLE", "Cannot reach the chat server: boom"
    )
    assert _check_matrix(_matrix_domain()) == "misconfigured"


@patch("app.modules.chat.services.matrix.MatrixClient")
@patch("app.admin.services.health_checks._tcp_check", return_value=True)
def test_matrix_unknown_probe_token_means_connected(mock_tcp, mock_client_cls):
    # A throwaway probe token gets 401 M_UNKNOWN_TOKEN when the homeserver
    # and its auth backend are healthy — that counts as connected.
    mock_client_cls.return_value.whoami.side_effect = MatrixError(
        "M_UNKNOWN_TOKEN", "Unrecognised access token", 401
    )
    assert _check_matrix(_matrix_domain()) == "connected"


@patch("app.modules.chat.services.matrix.MatrixClient")
@patch("app.admin.services.health_checks._tcp_check", return_value=True)
def test_matrix_probe_success_means_connected(mock_tcp, mock_client_cls):
    mock_client_cls.return_value.whoami.return_value = {"user_id": "@probe:server"}
    assert _check_matrix(_matrix_domain()) == "connected"


@patch("app.admin.services.health_checks._check_mail_api", return_value="not_configured")
@patch("app.admin.services.health_checks._collabora_check", return_value="not_configured")
@patch("app.modules.chat.services.matrix.MatrixClient")
@patch("app.admin.services.health_checks._tcp_check", return_value=True)
def test_check_domain_services_reports_auth_backend_down(
    mock_tcp, mock_client_cls, mock_collabora, mock_mail_api
):
    mock_client_cls.return_value.whoami.side_effect = MatrixError(
        "MATRIX_AUTH_BACKEND_DOWN",
        "The chat server's authentication backend is down",
        503,
    )
    domain = Domain()
    domain.name = "health.example.com"
    domain.is_active = True
    domain.status = "complete"
    domain.matrix_host = "synapse"
    domain.matrix_port = 8008
    result = check_domain_services(domain)
    assert result["matrix"] == "auth_backend_down"
