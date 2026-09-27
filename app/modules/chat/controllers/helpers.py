from __future__ import annotations

import logging

from flask import Blueprint, jsonify, session
from flask_babel import _

from app.shared.auth import require_customer
from app.shared.db import db
from app.shared.models.core import CustomerAccount, Domain

logger = logging.getLogger(__name__)


chat_bp = Blueprint("chat", __name__, template_folder="../templates")


class ChatApiError(Exception):
    """Structured chat error convertible to a JSON response."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def response(self):
        return jsonify({"error": {"code": self.code, "message": self.message}}), self.status


@chat_bp.errorhandler(ChatApiError)
def _handle_chat_api_error(exc: ChatApiError):
    return exc.response()


def _status_for_matrix(exc) -> int:
    code = getattr(exc, "code", "")
    if code == "MATRIX_NOT_CONFIGURED":
        return 503
    status = getattr(exc, "status", None)
    if status == 404:
        return 404
    return 502


def matrix_to_chat_error(exc, account_id: int) -> ChatApiError:
    """Map a MatrixError to a ChatApiError, logging account context."""
    logger.warning("chat matrix error account_id=%s code=%s", account_id, getattr(exc, "code", ""))
    status = 404 if getattr(exc, "status", None) == 404 else 502
    return ChatApiError(exc.code, exc.message, status)


def chat_context():
    """Return (account, user_id, conn, client, creds) for the active session.

    Raises ChatApiError on any failure. The caller owns the connection.
    """
    from app.modules.chat.services.provisioning import ensure_chat_client

    account, user_id, domain = current_account()
    from app.modules.chat.services.matrix import MatrixError

    try:
        conn, client, creds = ensure_chat_client(account, domain, user_id)
    except MatrixError as exc:
        logger.warning(
            "chat context error account_id=%s code=%s msg=%s", account.id, exc.code, exc.message
        )
        raise ChatApiError(exc.code, exc.message, _status_for_matrix(exc)) from exc
    return account, user_id, conn, client, creds


def current_account():
    """Return (account, user_id, domain) for the active session, no chat client.

    Used by endpoints that must work before the user's chat identity exists
    (e.g. the peer autocomplete when starting a first conversation).
    """
    account_id = session.get("active_account_id")
    user_id = session.get("user_id")
    if not account_id or not user_id:
        raise ChatApiError("NO_ACCOUNT", _("No active account for this session"), 404)
    account = db.session.get(CustomerAccount, account_id)
    if not account or account.customer_id != user_id or not account.is_active:
        raise ChatApiError("NO_ACCOUNT", _("No active account for this session"), 404)
    domain = db.session.get(Domain, account.domain_id)
    if not domain:
        raise ChatApiError("DOMAIN_NOT_FOUND", _("Account domain not found"), 404)
    return account, user_id, domain


def visible_domain_ids(domain) -> set[int]:
    """Domain ids whose accounts this domain's users may discover and message.

    Own domain plus the admin-configured allowlist (HLD U25.17); entries that
    are not integers are ignored. Same-homeserver enforcement is separate
    (see same_homeserver) and applied at query/DM-creation time.
    """
    ids = {domain.id}
    for value in getattr(domain, "chat_visible_domain_ids", None) or []:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def same_homeserver(domain, peer_domain) -> bool:
    return (peer_domain.matrix_host, peer_domain.matrix_port) == (
        domain.matrix_host,
        domain.matrix_port,
    )


def own_matrix_id(conn) -> str:
    from app.modules.chat.services.cache_db import get_credentials

    creds = get_credentials(conn)
    return creds["matrix_user_id"] if creds else ""


__all__ = [
    "ChatApiError",
    "chat_bp",
    "chat_context",
    "current_account",
    "matrix_to_chat_error",
    "own_matrix_id",
    "require_customer",
    "same_homeserver",
    "visible_domain_ids",
]
