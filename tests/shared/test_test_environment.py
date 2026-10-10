"""Test-environment guards.

The unit-test suite must stay single-threaded: background workers share
the in-memory SQLite connection with test fixtures and race the per-test
``_clean_db`` wipe. Symptom observed in the wild: intermittent, random
failures (~1 run in N, a different test each time) — ``/admin`` POSTs
redirecting to ``/admin/setup`` because the admin row momentarily wasn't
visible, and ``UNIQUE constraint failed: users.email`` in fixtures whose
rows had supposedly been wiped. Root cause: CalendarReminderWorker and
ChatPushWorker threads started for real inside every pytest session
(tests/conftest.py now mocks them alongside WorkerManager).
"""

import threading


def test_no_worker_threads_running_in_test_session(app):
    suspects = []
    for thread in threading.enumerate():
        if thread is threading.main_thread():
            continue
        target = getattr(thread, "_target", None)
        qualname = getattr(target, "__qualname__", "") or ""
        module = getattr(target, "__module__", "") or ""
        if module.startswith("app.workers.") or module == "app.shared.push":
            suspects.append(f"{thread.name}: {module}.{qualname}")
    assert not suspects, (
        "Background worker threads are running in the unit-test session; "
        "they race the per-test DB wipe and cause intermittent failures. "
        f"Threads: {suspects}"
    )
