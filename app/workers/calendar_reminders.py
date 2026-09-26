"""Calendar reminder push worker (U24.31).

Serves push-armed users with ``notify_calendar_enabled``: keeps their
encrypted calendar caches fresh via periodic headless CalDAV sync, then
fires one Web Push per due VALARM reminder, deduplicated through the
``push_calendar_fired`` table.

MVP limitation (documented in the HLD): recurring events fire reminders
for the master instance's dtstart only; server-side recurrence expansion
is out of scope.
"""

import logging
import re
import threading
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from app.shared import push, push_keys
from app.shared.db import db
from app.shared.i18n import forced_user_locale
from app.shared.models.core import (
    CustomerAccount,
    CustomerSettings,
    Domain,
    PushCalendarFired,
    PushSubscription,
)
from app.shared.timezone import resolve_user_timezone

logger = logging.getLogger(__name__)

LATE_FIRE_WINDOW = timedelta(hours=2)
FIRED_RETENTION = timedelta(days=30)
CAL_SYNC_INTERVAL = 900.0  # seconds between headless CalDAV syncs per account

_DURATION_RE = re.compile(
    r"^(?P<sign>[+-]?)P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_trigger_duration(text: str) -> timedelta | None:
    """Parse an RFC 5545 DURATION (e.g. ``-PT15M``, ``P1D``, ``PT0S``).

    Returns the signed offset to *add* to dtstart to get the fire time
    (negative = before the event). Returns None for unparsable values.
    """
    if not text:
        return None
    match = _DURATION_RE.match(text.strip().upper())
    if not match:
        return None
    parts = match.groupdict()
    if not any(parts[k] for k in ("weeks", "days", "hours", "minutes", "seconds")):
        return None
    delta = timedelta(
        weeks=int(parts["weeks"] or 0),
        days=int(parts["days"] or 0),
        hours=int(parts["hours"] or 0),
        minutes=int(parts["minutes"] or 0),
        seconds=int(parts["seconds"] or 0),
    )
    return -delta if parts["sign"] == "-" else delta


def _resolve_event_tz(event: dict, fallback_tz_name: str, vtimezones) -> tzinfo:
    """Event TZID -> tzinfo per U8.9 (IANA -> Windows map -> VTIMEZONE -> UTC)."""
    tzid = event.get("timezone") or ""
    if tzid:
        from app.shared.tzid_resolver import resolve_tzid

        tz = resolve_tzid(tzid, vtimezones=vtimezones)
        if tz is not None:
            return tz
        logger.warning(
            "calendar reminder: unresolvable TZID %r on event uid=%s; assuming UTC",
            tzid,
            event.get("uid"),
        )
        return UTC
    try:
        return ZoneInfo(fallback_tz_name)
    except Exception:
        return UTC


def _event_vtimezones(event: dict):
    raw_ical = event.get("raw_ical") or ""
    if not raw_ical:
        return None
    from app.shared.icalendar import parse_icalendar

    return parse_icalendar(raw_ical).get("vtimezones")


def _event_start_utc(event: dict, user_tz_name: str) -> datetime | None:
    raw = event.get("dtstart") or ""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        tz = _resolve_event_tz(event, user_tz_name, _event_vtimezones(event))
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


def _format_when_local(start_utc: datetime, user_tz_name: str, all_day: bool) -> str:
    """Format the reminder time. Must run inside ``forced_user_locale`` so the
    month/day names (and the "at" separator) follow the user's locale."""
    from flask_babel import _, format_datetime

    try:
        local = start_utc.astimezone(ZoneInfo(user_tz_name))
    except Exception:
        local = start_utc.astimezone(UTC)
    if all_day:
        return format_datetime(local, "EEE, MMM dd, y")
    return f"{format_datetime(local, 'EEE, MMM dd, y')} {_('at')} {format_datetime(local, 'HH:mm')}"


def _due_reminders(conn) -> list[dict]:
    """Join events with their reminders; column order: uid, summary, dtstart, all_day, timezone, raw_ical, trigger_val."""
    rows = conn.execute(
        """
        SELECT e.uid, e.summary, e.dtstart, e.all_day, e.timezone, e.raw_ical, r.trigger_val
        FROM calendar_events e
        JOIN calendar_reminders r ON r.event_id = e.id
        WHERE e.status != 'CANCELLED'
        """
    ).fetchall()
    return [
        {
            "uid": row[0],
            "summary": row[1],
            "dtstart": row[2],
            "all_day": bool(row[3]),
            "timezone": row[4],
            "raw_ical": row[5],
            "trigger_val": row[6],
        }
        for row in rows
    ]


class CalendarReminderWorker:
    def __init__(self, app, interval: float = 30.0, sync_interval: float = CAL_SYNC_INTERVAL):
        self.app = app
        self.interval = interval
        self.sync_interval = sync_interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._last_sync: dict[int, float] = {}

    def start(self):
        if not self._thread.is_alive():
            logger.info("calendar reminder worker starting")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("calendar reminder worker tick failed")
            self._stop.wait(self.interval)

    def tick(self, now: datetime | None = None):
        now = now or datetime.now(UTC)
        with self.app.app_context():
            for user_id, settings in self._eligible_users():
                accounts = CustomerAccount.query.filter_by(
                    customer_id=user_id, is_active=True
                ).all()
                for account in accounts:
                    try:
                        self._process_account(account, settings, now)
                    except Exception:
                        logger.exception(
                            "calendar reminders failed account_id=%s customer_id=%s",
                            account.id,
                            account.customer_id,
                        )

    def _eligible_users(self) -> list[tuple[int, CustomerSettings]]:
        """Users with calendar notifications on and at least one active push subscription."""
        subscribed = {
            row[0]
            for row in db.session.query(PushSubscription.user_id)
            .filter_by(disabled_at=None)
            .distinct()
            .all()
        }
        if not subscribed:
            return []
        rows = CustomerSettings.query.filter(
            CustomerSettings.customer_id.in_(subscribed),
            CustomerSettings.notify_calendar_enabled.is_(True),
        ).all()
        return [(s.customer_id, s) for s in rows]

    def _process_account(self, account: CustomerAccount, settings: CustomerSettings, now: datetime):
        if not account.cache_db_path:
            return
        try:
            dek = push_keys.ensure_push_key(account.customer_id)
        except Exception:
            logger.exception(
                "calendar reminders: push key unavailable customer_id=%s", account.customer_id
            )
            return
        if not dek:
            return
        from app.modules.calendar.services.cache_db import open_cache as open_calendar_cache

        try:
            conn = open_calendar_cache(account.cache_db_path, dek)
        except Exception:
            logger.exception(
                "calendar reminders: cache open failed account_id=%s customer_id=%s",
                account.id,
                account.customer_id,
            )
            return
        try:
            self._maybe_sync(account, conn, now)
            self._fire_due_reminders(account, settings, conn, now)
        finally:
            conn.close()

    def _maybe_sync(self, account: CustomerAccount, conn, now: datetime):
        """Headless CalDAV refresh every sync_interval per account (U24.31)."""
        last = self._last_sync.get(account.id, 0.0)
        if now.timestamp() - last < self.sync_interval:
            return
        self._last_sync[account.id] = now.timestamp()
        domain = db.session.get(Domain, account.domain_id)
        if not domain or not domain.is_active or not domain.caldav_host:
            return
        from app.modules.calendar.services.sync import sync_calendars_and_events
        from app.modules.mail.services.secrets import decrypt_with_key

        if not account.encrypted_secret:
            return
        key = push_keys.ensure_push_key(account.customer_id)
        if not key:
            return
        try:
            password = decrypt_with_key(account.encrypted_secret, key)
        except Exception:
            logger.exception(
                "calendar headless sync: credential decrypt failed account_id=%s", account.id
            )
            return
        scheme = (
            "https"
            if (domain.caldav_use_tls if domain.caldav_use_tls is not None else False)
            else "http"
        )
        base_url = f"{scheme}://{domain.caldav_host}:{domain.caldav_port or 5232}"
        try:
            sync_calendars_and_events(conn, account, base_url, password)
            logger.info("calendar headless sync complete account_id=%s", account.id)
        except Exception:
            logger.exception("calendar headless sync failed account_id=%s", account.id)

    def _fire_due_reminders(
        self,
        account: CustomerAccount,
        settings: CustomerSettings,
        conn,
        now: datetime,
    ):
        user_id = account.customer_id
        user_tz_name = resolve_user_timezone(settings.timezone if settings else "browser")
        for reminder in _due_reminders(conn):
            offset = parse_trigger_duration(reminder["trigger_val"])
            if offset is None:
                logger.warning(
                    "calendar reminder: unparsable trigger=%r uid=%s account_id=%s",
                    reminder["trigger_val"],
                    reminder["uid"],
                    account.id,
                )
                continue
            start_utc = _event_start_utc(reminder, user_tz_name)
            if start_utc is None:
                continue
            fire_at = start_utc + offset
            if fire_at > now or fire_at < now - LATE_FIRE_WINDOW:
                continue
            key = (account.id, reminder["uid"], reminder["trigger_val"])
            exists = (
                db.session.query(PushCalendarFired.id)
                .filter_by(account_id=key[0], event_uid=key[1], trigger_val=key[2])
                .first()
            )
            if exists:
                continue
            with forced_user_locale(user_id):
                when_local = _format_when_local(start_utc, user_tz_name, reminder["all_day"])
            sent = push.send_calendar_push(
                self.app,
                user_id,
                reminder["uid"],
                reminder["summary"],
                when_local,
            )
            fired = PushCalendarFired()
            fired.account_id = key[0]
            fired.event_uid = key[1]
            fired.trigger_val = key[2]
            db.session.add(fired)
            db.session.commit()
            logger.info(
                "calendar reminder %s account_id=%s uid=%s trigger=%s",
                "pushed" if sent else "skipped (no device/category)",
                account.id,
                reminder["uid"],
                reminder["trigger_val"],
            )
        self._prune_fired(now)

    def _prune_fired(self, now: datetime):
        cutoff = now - FIRED_RETENTION
        try:
            deleted = (
                db.session.query(PushCalendarFired)
                .filter(PushCalendarFired.fired_at < cutoff)
                .delete(synchronize_session=False)
            )
            if deleted:
                db.session.commit()
                logger.info("calendar reminder fired rows pruned count=%s", deleted)
        except Exception:
            db.session.rollback()
            logger.exception("calendar reminder prune failed")
