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


def chat_context():
    """Return (account, user_id, conn, client, creds) for the active session.

    Raises ChatApiError on any failure. The caller owns the connection.
    """
    from app.modules.chat.services.provisioning import ensure_chat_client

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

    from app.modules.chat.services.matrix import MatrixError

    try:
        conn, client, creds = ensure_chat_client(account, domain, user_id)
    except MatrixError as exc:
        logger.warning(
            "chat context error account_id=%s code=%s msg=%s", account.id, exc.code, exc.message
        )
        raise ChatApiError(exc.code, exc.message, _status_for_matrix(exc)) from exc
    return account, user_id, conn, client, creds


def own_matrix_id(conn) -> str:
    from app.modules.chat.services.cache_db import get_credentials

    creds = get_credentials(conn)
    return creds["matrix_user_id"] if creds else ""


__all__ = ["ChatApiError", "chat_bp", "chat_context", "own_matrix_id", "require_customer"]
