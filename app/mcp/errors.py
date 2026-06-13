from __future__ import annotations

import functools
import json
import logging
import uuid
from typing import Any, Callable

from app.mcp.auth import McpAuthError
from app.shared.cache_errors import CacheKeyMismatchError

_logger = logging.getLogger(__name__)

EXPECTED_TOOL_COUNT = 50

_AUTH_ERROR_CODES = {
    "AUTH_INVALID",
    "NO_ACCOUNT",
    "NO_DEK",
    "API_DISABLED",
    "SCOPE_DENIED",
    "NOT_FOUND",
    "FORBIDDEN",
    "NOT_CONFIGURED",
}


def _build_known_error_map():
    import imaplib
    import smtplib
    import socket
    import ssl

    try:
        from cryptography.fernet import InvalidToken
    except ImportError:
        InvalidToken = None

    mapping: list[tuple[type[Exception], str, str, str | None]] = [
        (
            CacheKeyMismatchError,
            "CACHE_KEY_MISMATCH",
            "Your encrypted cache cannot be opened because the encryption key does not match. "
            "This can happen after a password change, a server upgrade, or an API key rotation. "
            "To fix this: go to Settings \u2192 API \u2192 Disable API access, then re-enable it and create a new API token. "
            "This resets your encryption keys and re-syncs your data from the mail server.",
            "Disable API access in Settings, re-enable it, and create a new API token.",
        ),
        (
            ImportError,
            "SERVICE_UNAVAILABLE",
            "A required service module could not be loaded. The server may need to be restarted or reconfigured.",
            None,
        ),
    ]

    if InvalidToken is not None:
        mapping.append((
            InvalidToken,
            "DEK_MISMATCH",
            "Your encryption key does not match the stored credentials. "
            "Reset your API access: go to Settings \u2192 API \u2192 Disable, then re-enable and create a new token.",
            "Disable API access in Settings, re-enable it, and create a new API token.",
        ))

    mapping.extend([
        (
            imaplib.IMAP4.error,
            "IMAP_ERROR",
            "The mail server returned an error. "
            "Your password may have changed, or the server rejected the operation. "
            "Try resetting your API access in Settings.",
            None,
        ),
        (
            smtplib.SMTPAuthenticationError,
            "SMTP_AUTH_FAILED",
            "Mail server authentication failed when sending. Your password may have changed. "
            "Reset your API access in Settings to update stored credentials.",
            None,
        ),
        (
            smtplib.SMTPRecipientsRefused,
            "SMTP_RECIPIENT_REFUSED",
            "The mail server rejected one or more recipients. Check that all email addresses are valid.",
            None,
        ),
        (
            smtplib.SMTPException,
            "SMTP_ERROR",
            "Failed to send email through the mail server. The server may be temporarily unavailable.",
            None,
        ),
        (
            ConnectionRefusedError,
            "SERVICE_UNAVAILABLE",
            "Could not connect to the server. The service may be down or the connection is blocked.",
            None,
        ),
        (
            socket.timeout,
            "SERVICE_UNAVAILABLE",
            "Connection to the server timed out. The service may be slow or temporarily unavailable.",
            None,
        ),
        (
            ssl.SSLError,
            "TLS_ERROR",
            "Could not establish a secure connection to the server. "
            "There may be a certificate or configuration issue.",
            None,
        ),
        (
            ConnectionResetError,
            "SERVICE_UNAVAILABLE",
            "The connection to the server was reset. The service may be restarting.",
            None,
        ),
    ])

    return mapping


_KNOWN_ERROR_MAP = _build_known_error_map()


def structured_error(
    code: str,
    message: str,
    details: str | None = None,
    request_id: str | None = None,
) -> str:
    if request_id is None:
        request_id = uuid.uuid4().hex[:12]
    error_obj: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if details:
        error_obj["error"]["details"] = details
    return json.dumps(error_obj)


def _auth_error(exc: McpAuthError, tool_name: str) -> str:
    request_id = uuid.uuid4().hex[:12]
    code = exc.code if exc.code in _AUTH_ERROR_CODES else "AUTH_ERROR"
    _logger.warning(
        "auth error in tool %s request_id=%s code=%s: %s",
        tool_name, request_id, code, exc.message,
    )
    return structured_error(code, exc.message, request_id=request_id)


def _resolve_known_error(exc: Exception, tool_name: str, request_id: str) -> str | None:
    if type(exc).__name__ == "_ServiceConnectionError":
        _logger.exception(
            "service connection error in tool %s request_id=%s: %s",
            tool_name, request_id, exc,
        )
        svc = getattr(exc, "service", "server")
        host = getattr(exc, "host", "unknown")
        orig = getattr(exc, "original", exc)
        orig_type = type(orig).__name__
        message = f"Could not connect to the {svc} server at {host} ({orig_type}: {orig}). "
        if "AUTH" in str(orig).upper() or "LOGIN" in str(orig).upper() or "auth" in orig_type.lower():
            message += "Authentication failed — your password may have changed. Reset your API access in Settings."
        else:
            message += "The server may be down, unreachable, or refusing connections."
        return structured_error("SERVICE_UNAVAILABLE", message, request_id=request_id)
    for exc_type, code, message, details in _KNOWN_ERROR_MAP:
        if isinstance(exc, exc_type):
            _logger.exception(
                "known error in tool %s request_id=%s code=%s: %s",
                tool_name, request_id, code, exc,
            )
            return structured_error(code, message, details=details, request_id=request_id)
    return None


def resilient_tool(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            result = await func(*args, **kwargs)
            if not isinstance(result, str):
                return json.dumps({"data": result})
            return result
        except McpAuthError as exc:
            return _auth_error(exc, func.__name__)
        except Exception as exc:
            request_id = uuid.uuid4().hex[:12]
            known = _resolve_known_error(exc, func.__name__, request_id)
            if known:
                return known
            _logger.exception("tool %s failed request_id=%s", func.__name__, request_id)
            return structured_error(
                "INTERNAL_ERROR",
                f"An internal error occurred (request_id={request_id}). "
                "Please retry. If the problem persists, contact support and quote this request_id.",
                request_id=request_id,
            )
    return wrapper


def get_registry_snapshot(mcp: Any) -> dict[str, Any]:
    try:
        tools = mcp._tool_manager._tools
        registered = sorted(tools.keys()) if tools else []
        return {
            "registered_tools": len(registered),
            "tool_names": registered,
        }
    except Exception:
        return {"registered_tools": 0, "tool_names": []}


def health_check(mcp: Any, session_valid: bool = True) -> dict[str, Any]:
    registry = get_registry_snapshot(mcp)
    return {
        "healthy": True,
        "registered_tools": registry["registered_tools"],
        "session_valid": session_valid,
    }
