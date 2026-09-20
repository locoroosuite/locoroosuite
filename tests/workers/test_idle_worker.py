"""Tests for _IdleWorker retry policy (U4.3/U4.4).

U4.4: a single transient handshake failure or connection error must not
permanently downgrade an account to polling; only 3 consecutive handshake
failures do (approved product decision).
"""

import threading
from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

from app.workers.manager import _IdleWorker, WorkerManager


class _RecordingStop(threading.Event):
    """Event whose wait() returns immediately and records the delays."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[Any] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        return self.is_set()


def _run_worker(app: Any, account_id: int, idle_side_effect: list[Any]) -> tuple[Any, Any, Any, Any]:
    wm = WorkerManager(app)
    worker = _IdleWorker(app, wm, account_id, "INBOX")
    stop = _RecordingStop()
    worker._stop = stop

    def stop_and_idle(*_args: Any, **_kwargs: Any) -> tuple[bool, None]:
        stop.set()
        return (True, None)

    idle = MagicMock(
        side_effect=[stop_and_idle if item == "STOP" else item for item in idle_side_effect]
    )
    connect_mock = MagicMock()
    enqueue = MagicMock()
    set_supported = MagicMock()
    with app.app_context():
        with ExitStack() as stack:
            for p in (
                patch("app.workers.manager.connect_imap", connect_mock),
                patch("app.workers.manager.login_imap", MagicMock()),
                patch("app.workers.manager.select_folder", MagicMock()),
                patch("app.workers.manager.safe_logout", MagicMock()),
                patch("app.workers.manager.decrypt_with_key", MagicMock(return_value="pw")),
                patch("app.workers.manager.idle_wait", idle),
                patch.object(wm, "emit_status", MagicMock()),
                patch.object(wm, "enqueue_sync", enqueue),
                patch.object(wm, "set_idle_supported", set_supported),
            ):
                stack.enter_context(p)
            worker._run()
    return stop, connect_mock, enqueue, set_supported


class TestIdleWorkerRetryPolicy:
    def test_two_handshake_failures_then_success_keeps_idle(self, app, authed_client):
        _client, _user_id, account_id = authed_client
        stop, connect_mock, _enqueue, set_supported = _run_worker(
            app, account_id, [(False, b"T1 BAD"), (False, b"T2 BAD"), "STOP"]
        )
        # Reconnects after each failure: 3 connections total, still on IDLE.
        assert connect_mock.call_count == 3
        set_supported.assert_not_called()
        assert stop.waits == [5]

    def test_three_consecutive_failures_fall_back_to_polling(self, app, authed_client):
        _client, _user_id, account_id = authed_client
        stop, _connect, _enqueue, set_supported = _run_worker(
            app,
            account_id,
            [(False, b"T1 BAD"), (False, b"T2 BAD"), (False, b"T3 BAD")],
        )
        set_supported.assert_called_once_with(account_id, False)
        assert stop.waits == [5, 5]

    def test_connection_error_reconnects_without_disabling(self, app, authed_client):
        _client, _user_id, account_id = authed_client
        stop, connect_mock, _enqueue, set_supported = _run_worker(
            app, account_id, [ConnectionResetError("peer closed"), "STOP"]
        )
        set_supported.assert_not_called()
        assert connect_mock.call_count == 2
        assert stop.waits == [5]

    def test_new_mail_response_enqueues_sync(self, app, authed_client):
        _client, _user_id, account_id = authed_client
        _stop, _connect, enqueue, _set_supported = _run_worker(
            app, account_id, [(True, b"* 5 EXISTS"), "STOP"]
        )
        enqueue.assert_called_once_with(account_id, folder="INBOX", reason="idle", priority=5)
