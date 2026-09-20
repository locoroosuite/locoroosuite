"""Tests for the calendar reminder push worker (U24.31)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.shared.db import db
from app.shared.keys import get_user_key
from app.shared.models.core import (
    CustomerAccount,
    CustomerSettings,
    PushCalendarFired,
    PushSubscription,
)
from app.workers.calendar_reminders import (
    CalendarReminderWorker,
    parse_trigger_duration,
)


def _make_subscription(user_id, endpoint="https://push.example.com/sub/cal"):
    row = PushSubscription()
    row.user_id = user_id
    row.endpoint = endpoint
    row.p256dh = "p256dh-key"
    row.auth = "auth-key"
    row.user_agent = "pytest-agent"
    db.session.add(row)
    db.session.commit()
    return row


def _ical(uid: str, dtstart_line: str, summary: str, trigger: str = "-PT15M") -> str:
    """Minimal VCALENDAR/VVEVENT/VVALARM text understood by parse_icalendar.

    ``dtstart_line`` is the full DTSTART property line, e.g.
    ``DTSTART:20260920T094500Z`` or ``DTSTART;TZID=Europe/Madrid:20260920T120000``.
    """
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//LocoRoo//Test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"{dtstart_line}\r\n"
        f"SUMMARY:{summary}\r\n"
        "BEGIN:VALARM\r\n"
        "ACTION:DISPLAY\r\n"
        f"TRIGGER:{trigger}\r\n"
        "DESCRIPTION:Reminder\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def _seed_worker_env(app, user_id, account, tmp_path, icals):
    """Create settings, subscription, and a real encrypted calendar cache."""
    settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
    if not settings:
        settings = CustomerSettings()
        settings.customer_id = user_id
        db.session.add(settings)
    settings.notify_calendar_enabled = True
    settings.timezone = "UTC"
    _make_subscription(user_id)

    from app.modules.calendar.services.cache_db import open_cache, upsert_calendar, upsert_event

    cache_path = str(tmp_path / f"cal_{account.id}.db")
    account.cache_db_path = cache_path
    db.session.commit()
    conn = open_cache(cache_path, get_user_key(user_id))
    try:
        cal_id = upsert_calendar(conn, "cal-uid-1", "/test/cal1/", displayname="Work")
        uids = []
        for i, ical_text in enumerate(icals):
            uid = f"uid-{i + 1}"
            upsert_event(conn, uid, f"/test/cal1/{uid}.ics", f"etag-{i}", cal_id, ical_text)
            uids.append(uid)
        return uids
    finally:
        conn.close()


class TestParseTriggerDuration:
    def test_common_formats(self):
        assert parse_trigger_duration("-PT15M") == timedelta(minutes=-15)
        assert parse_trigger_duration("-PT0M") == timedelta(0)
        assert parse_trigger_duration("-P1D") == timedelta(days=-1)
        assert parse_trigger_duration("-PT1H30M") == timedelta(minutes=-90)
        assert parse_trigger_duration("PT10M") == timedelta(minutes=10)
        assert parse_trigger_duration("-P1W") == timedelta(weeks=-1)

    def test_invalid(self):
        assert parse_trigger_duration("") is None
        assert parse_trigger_duration("nope") is None
        assert parse_trigger_duration("P") is None


class TestFireDueReminders:
    def test_fires_once_for_due_event(self, app, authed_client, tmp_path):
        _client, user_id, account_id = authed_client
        now = datetime.now(UTC)
        due_start = "DTSTART:" + (now + timedelta(minutes=10)).strftime(
            "%Y%m%dT%H%M%SZ"
        )  # -PT15M -> 5 min ago
        future_start = "DTSTART:" + (now + timedelta(days=3)).strftime("%Y%m%dT%H%M%SZ")
        stale_start = "DTSTART:" + (now - timedelta(days=2)).strftime("%Y%m%dT%H%M%SZ")
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            _seed_worker_env(
                app,
                user_id,
                account,
                tmp_path,
                [
                    _ical("due", due_start, "Standup"),
                    _ical("far", future_start, "Future planning"),
                    _ical("stale", stale_start, "Old meeting"),
                ],
            )
            worker = CalendarReminderWorker(app)
            with patch("app.workers.calendar_reminders.push.send_calendar_push") as send:
                worker.tick(now=now)
                assert send.call_count == 1
                uid_arg = send.call_args.args[2]
                assert uid_arg == "uid-1"
                assert send.call_args.args[3] == "Standup"
                assert "at" in send.call_args.args[4]  # formatted local time
                fired = db.session.query(PushCalendarFired).all()
                assert len(fired) == 1
                assert fired[0].event_uid == "uid-1"
                assert fired[0].trigger_val == "-PT15M"
                # Second tick: deduped, no further sends.
                worker.tick(now=now + timedelta(seconds=5))
                assert send.call_count == 1
                assert db.session.query(PushCalendarFired).count() == 1

    def test_respects_tzid_and_all_day(self, app, authed_client, tmp_path):
        _client, user_id, account_id = authed_client
        now = datetime.now(UTC)
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            # Naive local 12:00 in Madrid (UTC+2 in September) with -PT30M
            # reminder: fire_at = 09:30 UTC. Build so it is due now.
            fire_at_utc = now - timedelta(minutes=1)
            from zoneinfo import ZoneInfo

            local = fire_at_utc.astimezone(ZoneInfo("Europe/Madrid")) + timedelta(minutes=30)
            dtstart = "DTSTART;TZID=Europe/Madrid:" + local.strftime("%Y%m%dT%H%M%S")
            _seed_worker_env(
                app, user_id, account, tmp_path, [_ical("tz", dtstart, "Madrid event", "-PT30M")]
            )
            worker = CalendarReminderWorker(app)
            with patch("app.workers.calendar_reminders.push.send_calendar_push") as send:
                worker.tick(now=now)
            assert send.call_count == 1

    def test_no_subscription_no_send(self, app, authed_client, tmp_path):
        _client, user_id, account_id = authed_client
        now = datetime.now(UTC)
        due_start = "DTSTART:" + (now + timedelta(minutes=10)).strftime("%Y%m%dT%H%M%SZ")
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            _seed_worker_env(app, user_id, account, tmp_path, [_ical("due", due_start, "Standup")])
            PushSubscription.query.filter_by(user_id=user_id).delete()
            db.session.commit()
            worker = CalendarReminderWorker(app)
            with patch("app.workers.calendar_reminders.push.send_calendar_push") as send:
                worker.tick(now=now)
            send.assert_not_called()
            assert db.session.query(PushCalendarFired).count() == 0

    def test_calendar_category_disabled_no_send(self, app, authed_client, tmp_path):
        _client, user_id, account_id = authed_client
        now = datetime.now(UTC)
        due_start = "DTSTART:" + (now + timedelta(minutes=10)).strftime("%Y%m%dT%H%M%SZ")
        with app.app_context():
            account = db.session.get(CustomerAccount, account_id)
            _seed_worker_env(app, user_id, account, tmp_path, [_ical("due", due_start, "Standup")])
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            if settings is None:
                raise AssertionError("settings missing after seed")
            settings.notify_calendar_enabled = False
            db.session.commit()
            worker = CalendarReminderWorker(app)
            with patch("app.workers.calendar_reminders.push.send_calendar_push") as send:
                worker.tick(now=now)
            send.assert_not_called()
            assert db.session.query(PushCalendarFired).count() == 0
