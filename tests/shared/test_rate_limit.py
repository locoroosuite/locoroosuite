from datetime import datetime, timedelta, timezone

from app.shared.db import db
from app.shared.models.core import LoginAttempt
from app.shared.rate_limit import record_failed_login, is_locked, clear_failed_login


def _seed_attempt(app, username="test@example.com", ip="127.0.0.1", failed_count=1, first_failed_at=None, locked_until=None):
    with app.app_context():
        attempt = LoginAttempt(
            username=username,
            ip_address=ip,
            failed_count=failed_count,
            first_failed_at=first_failed_at or datetime.now(timezone.utc),
            locked_until=locked_until,
        )
        db.session.add(attempt)
        db.session.commit()
        return attempt.id


class TestRecordFailedLoginNaiveDatetime:
    def test_second_failure_after_sqlite_roundtrip(self, app, _clean_db):
        _seed_attempt(app)
        with app.app_context():
            attempt = LoginAttempt.query.first()
            assert attempt.first_failed_at.tzinfo is None
        with app.app_context():
            record_failed_login("test@example.com", "127.0.0.1")
            attempt = LoginAttempt.query.first()
            assert attempt.failed_count == 2

    def test_is_locked_with_naive_locked_until(self, app, _clean_db):
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        _seed_attempt(app, locked_until=future, failed_count=10)
        with app.app_context():
            attempt = LoginAttempt.query.first()
            assert attempt.locked_until.tzinfo is None
        with app.app_context():
            assert is_locked("test@example.com", "127.0.0.1") is True

    def test_is_locked_expired(self, app, _clean_db):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        _seed_attempt(app, locked_until=past, failed_count=10)
        with app.app_context():
            assert is_locked("test@example.com", "127.0.0.1") is False

    def test_window_resets_after_fail_window(self, app, _clean_db):
        old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        _seed_attempt(app, failed_count=8, first_failed_at=old_time)
        with app.app_context():
            record_failed_login("test@example.com", "127.0.0.1")
            attempt = LoginAttempt.query.first()
            assert attempt.failed_count == 1

    def test_clear_removes_attempt(self, app, _clean_db):
        _seed_attempt(app)
        with app.app_context():
            clear_failed_login("test@example.com", "127.0.0.1")
            assert LoginAttempt.query.first() is None
