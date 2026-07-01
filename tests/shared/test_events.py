import logging

from app.shared import events


def test_event_queue_overflow_drops_oldest_and_warns(caplog):
    caplog.set_level(logging.WARNING, logger="app.shared.events")
    uid = 9999
    try:
        for i in range(events.MAX_QUEUE_SIZE + 10):
            events.push_event(uid, "sync_status", {"i": i})
        q = events._event_queues[uid]
        assert q.qsize() <= events.MAX_QUEUE_SIZE
        assert any("sse event queue overflow" in r.getMessage() for r in caplog.records)
    finally:
        events._event_queues.pop(uid, None)


def test_event_queue_no_warning_when_below_threshold(caplog):
    caplog.set_level(logging.WARNING, logger="app.shared.events")
    uid = 8888
    try:
        for i in range(5):
            events.push_event(uid, "sync_status", {"i": i})
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    finally:
        events._event_queues.pop(uid, None)
