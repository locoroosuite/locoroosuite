"""In-process registry of open chat SSE streams (HLD U25.61).

The headless push worker skips users whose chat tab is already driving
sync, so the two loops never compete for the shared Matrix sync token.
Refcounted: multiple tabs on one account register/unregister cleanly.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_streams: dict[int, int] = {}  # user_id -> open stream count


def register_stream(user_id: int) -> None:
    with _lock:
        _streams[user_id] = _streams.get(user_id, 0) + 1


def unregister_stream(user_id: int) -> None:
    with _lock:
        count = _streams.get(user_id, 0) - 1
        if count > 0:
            _streams[user_id] = count
        else:
            _streams.pop(user_id, None)


def has_active_stream(user_id: int) -> bool:
    with _lock:
        return user_id in _streams
