import logging
import queue
from collections import defaultdict

_logger = logging.getLogger(__name__)

_event_queues = defaultdict(queue.Queue)

MAX_QUEUE_SIZE = 200


def push_event(user_id, event_type, data):
    if user_id is None:
        return
    q = _event_queues[user_id]
    q.put({"type": event_type, "data": data})
    size = q.qsize()
    if size > MAX_QUEUE_SIZE:
        try:
            dropped = q.get_nowait()
        except queue.Empty:
            dropped = None
        _logger.warning(
            "sse event queue overflow user_id=%s event_type=%s size=%s dropped_type=%s",
            user_id,
            event_type,
            size,
            dropped.get("type") if dropped else None,
        )
    elif size >= MAX_QUEUE_SIZE // 2 and size % 25 == 0:
        _logger.info(
            "sse event queue growing user_id=%s size=%s last_type=%s",
            user_id,
            size,
            event_type,
        )


def stream_events(user_id, timeout=30):
    q = _event_queues[user_id]
    try:
        event = q.get(timeout=timeout)
        return event
    except queue.Empty:
        return None
