import json
import logging
from flask import Response, session, stream_with_context

from app.shared.auth import require_customer
from app.shared.events import stream_events

from app.modules.mail.controllers.helpers import mail_sse_bp


logger = logging.getLogger(__name__)


@mail_sse_bp.route("/stream")
@require_customer
def stream():
    user_id = session.get("user_id")

    @stream_with_context
    def event_stream():
        logger.info("sse stream opened user_id=%s", user_id)
        try:
            while True:
                event = stream_events(user_id)
                if event:
                    payload = json.dumps(event.get("data", {}))
                    event_type = event.get("type", "message")
                    yield f"event: {event_type}\ndata: {payload}\n\n"
                else:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            logger.info("sse stream closed user_id=%s", user_id)

    return Response(event_stream(), mimetype="text/event-stream")
