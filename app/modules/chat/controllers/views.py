from __future__ import annotations

import logging

from flask import render_template

from app.modules.chat.controllers.helpers import chat_bp, chat_context, require_customer

logger = logging.getLogger(__name__)


@chat_bp.route("/chat/")
@require_customer
def index():
    identity_error = None
    try:
        _account, _user_id, conn, _client, _creds = chat_context()
        try:
            pass
        finally:
            conn.close()
    except Exception as exc:
        code = getattr(exc, "code", "CHAT_UNAVAILABLE")
        message = getattr(exc, "message", str(exc))
        identity_error = {"code": code, "message": message}
        logger.warning("chat index provisioning failed code=%s", code)
    return render_template("chat/index.html", identity_error=identity_error)
