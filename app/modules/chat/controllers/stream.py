"""Chat SSE stream: drives Matrix /sync long-polling and pushes changes."""

from __future__ import annotations

import json
import logging
import time

from flask import Response

from app.modules.chat.controllers.helpers import chat_bp, chat_context, require_customer
from app.modules.chat.services import cache_db
from app.modules.chat.services.matrix import MatrixError
from app.modules.chat.services.sync import process_sync, sync_lock

logger = logging.getLogger(__name__)

_SYNC_TIMEOUT_MS = 25000
_ERROR_BACKOFF_S = 3
_MAX_CONSECUTIVE_ERRORS = 5


@chat_bp.route("/chat/api/stream")
@require_customer
def stream():
    # All request-scoped state (account, conn, client, creds) is captured
    # before the generator starts, so no stream_with_context is needed.
    account, user_id, conn, client, creds = chat_context()
    own_id = creds.get("matrix_user_id") or ""
    # Translated here (request context): the generator below runs without one.
    from flask_babel import _

    sync_failed_msg = _("Chat sync failed; retrying.")

    def event_stream():
        logger.info("chat sse stream opened user_id=%s account_id=%s", user_id, account.id)
        lock = sync_lock(user_id)
        consecutive_errors = 0
        yield "event: chat_ready\ndata: {}\n\n"
        try:
            while True:
                try:
                    since_state = cache_db.get_sync_state(conn)
                    since = since_state.get("since_token") if since_state else None
                    resp = client.sync(since=since, timeout_ms=_SYNC_TIMEOUT_MS)
                    with lock:
                        changes = process_sync(conn, own_id, resp)
                    consecutive_errors = 0
                    yield f"event: chat_sync\ndata: {json.dumps(changes)}\n\n"
                except MatrixError as exc:
                    consecutive_errors += 1
                    logger.warning(
                        "chat sync error user_id=%s code=%s attempt=%s",
                        user_id,
                        exc.code,
                        consecutive_errors,
                    )
                    yield (
                        "event: chat_error\ndata: "
                        + json.dumps({"code": exc.code, "message": exc.message})
                        + "\n\n"
                    )
                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        logger.warning(
                            "chat sse stream stopping user_id=%s after %s errors",
                            user_id,
                            consecutive_errors,
                        )
                        return
                    time.sleep(_ERROR_BACKOFF_S)
                except Exception:
                    # Unexpected error (cache/db): keep the stream alive instead of
                    # silently dying, so the client sees a typed error and the log
                    # keeps the traceback with context.
                    consecutive_errors += 1
                    logger.exception(
                        "chat sync crashed user_id=%s account_id=%s attempt=%s",
                        user_id,
                        account.id,
                        consecutive_errors,
                    )
                    yield (
                        "event: chat_error\ndata: "
                        + json.dumps({"code": "SYNC_FAILED", "message": sync_failed_msg})
                        + "\n\n"
                    )
                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        logger.warning(
                            "chat sse stream stopping user_id=%s after %s unexpected errors",
                            user_id,
                            consecutive_errors,
                        )
                        return
                    time.sleep(_ERROR_BACKOFF_S)
        finally:
            conn.close()
            logger.info("chat sse stream closed user_id=%s", user_id)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
