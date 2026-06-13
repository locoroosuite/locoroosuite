from datetime import datetime, timedelta, timezone

from app.shared.db import db
from app.shared.models.core import LoginAttempt


FAIL_WINDOW = timedelta(minutes=10)
BACKOFF_STEPS = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15)]
LOCK_AFTER = 10
LOCK_DURATION = timedelta(minutes=30)


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_locked(username, ip_address):
    attempt = LoginAttempt.query.filter_by(username=username, ip_address=ip_address).first()
    if not attempt or not attempt.locked_until:
        return False
    if _aware(attempt.locked_until) < datetime.now(timezone.utc):
        attempt.locked_until = None
        db.session.commit()
        return False
    return True


def record_failed_login(username, ip_address):
    now = datetime.now(timezone.utc)
    attempt = LoginAttempt.query.filter_by(username=username, ip_address=ip_address).first()
    if not attempt:
        attempt = LoginAttempt(username=username, ip_address=ip_address, failed_count=1, first_failed_at=now)
        db.session.add(attempt)
        db.session.commit()
        return

    if attempt.first_failed_at and now - _aware(attempt.first_failed_at) > FAIL_WINDOW:
        attempt.failed_count = 1
        attempt.first_failed_at = now
    else:
        attempt.failed_count += 1

    if attempt.failed_count >= LOCK_AFTER:
        attempt.locked_until = now + LOCK_DURATION
    elif attempt.failed_count >= 5:
        backoff_index = min(attempt.failed_count - 5, len(BACKOFF_STEPS) - 1)
        attempt.locked_until = now + BACKOFF_STEPS[backoff_index]

    attempt.updated_at = now
    db.session.commit()


def clear_failed_login(username, ip_address):
    attempt = LoginAttempt.query.filter_by(username=username, ip_address=ip_address).first()
    if attempt:
        db.session.delete(attempt)
        db.session.commit()
