import queue
from collections import defaultdict


_event_queues = defaultdict(queue.Queue)


def push_event(user_id, event_type, data):
    if user_id is None:
        return
    _event_queues[user_id].put({"type": event_type, "data": data})


def stream_events(user_id, timeout=30):
    q = _event_queues[user_id]
    try:
        event = q.get(timeout=timeout)
        return event
    except queue.Empty:
        return None
