"""Server-side recurrence expansion (U12.56f).

Expands VEVENT recurrence (RRULE, RDATE, EXDATE, RECURRENCE-ID overrides)
into concrete occurrences within a requested time range, so every calendar
view renders recurring series without duplicating RFC 5545 math in JS.

Stored datetimes are ISO strings. All-day events use bare dates
("YYYY-MM-DD"); timed events are naive local wall-clock times when the
event carries a TZID, or offset-aware ISO strings for UTC events. The
expander works in the event's own frame (naive) and preserves the event's
duration across occurrences.
"""

import json
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 2000
MAX_HORIZON_YEARS = 3

_WEEKDAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_WEEKDAY_NAMES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def parse_rrule(rrule):
    """Parse an RRULE value string into a dict of uppercase parts."""
    parts = {}
    if not rrule:
        return parts
    for chunk in rrule.split(";"):
        chunk = chunk.strip()
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parts[key.strip().upper()] = value.strip()
    return parts


def _parse_ical_dt(value):
    """Parse an iCalendar-format or ISO datetime into a naive datetime.

    Returns None when unparseable. Trailing "Z" / ISO offsets are stripped:
    comparisons happen in the event's own wall-clock frame (good enough for
    expansion; DST-shift precision is not required for grid rendering).
    """
    if not value:
        return None
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        try:
            return datetime.strptime(value, "%Y%m%d")
        except ValueError:
            return None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _parse_bound(value, end_of_day=False):
    """Parse a range bound (date or datetime string) into a naive datetime.

    Date-only bounds are inclusive: start -> midnight, end -> next midnight
    when end_of_day is set (callers pass exclusive-end dates for month views).
    """
    if not value:
        return None
    value = value.strip()
    if len(value) == 10:
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return None
        base = datetime(d.year, d.month, d.day)
        return base + timedelta(days=1) if end_of_day else base
    parsed = _parse_ical_dt(value)
    if parsed and end_of_day and len(value) <= 10:
        return parsed + timedelta(days=1)
    return parsed


def _to_exdate_keys(values):
    """Normalize EXDATE/RDATE values to comparable minute-precision keys."""
    keys = set()
    for raw in values:
        parsed = _parse_ical_dt(raw)
        if parsed is not None:
            keys.add(parsed.strftime("%Y%m%dT%H%M"))
    return keys


def _load_date_list(raw):
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [str(v) for v in raw if v]


def _month_occurrences(year, month, rule, anchor):
    """Yield candidate day-of-month values for a MONTHLY-style rule."""
    bymonthday = rule.get("BYMONTHDAY", "")
    byday = rule.get("BYDAY", "")
    bysetpos = rule.get("BYSETPOS", "")
    if byday:
        wanted = []
        for token in byday.split(","):
            token = token.strip().upper()
            if token in _WEEKDAY_CODES:
                wanted.append(_WEEKDAY_CODES[token])
        if not wanted:
            return [anchor.day]
        days = []
        if month == 2:
            last = 29 if _is_leap(year) else 28
        elif month in (4, 6, 9, 11):
            last = 30
        else:
            last = 31
        for day in range(1, last + 1):
            if date(year, month, day).weekday() in wanted:
                days.append(day)
        if bysetpos:
            positions = []
            for token in bysetpos.split(","):
                try:
                    positions.append(int(token.strip()))
                except ValueError:
                    continue
            selected = []
            for pos in positions:
                if pos > 0 and pos <= len(days):
                    selected.append(days[pos - 1])
                elif pos < 0 and -pos <= len(days):
                    selected.append(days[pos])
            return sorted(set(selected))
        return days
    if bymonthday:
        days = []
        for token in bymonthday.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                dom = int(token)
            except ValueError:
                continue
            if dom > 0:
                try:
                    date(year, month, dom)
                except ValueError:
                    continue
                days.append(dom)
            elif dom < 0:
                if month == 2:
                    last = 29 if _is_leap(year) else 28
                elif month in (4, 6, 9, 11):
                    last = 30
                else:
                    last = 31
                dom_abs = last + dom + 1
                if 1 <= dom_abs <= last:
                    days.append(dom_abs)
        return sorted(set(days))
    return [anchor.day]


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _iter_rrule_starts(event):
    """Yield naive start datetimes for an event's RRULE, from DTSTART on.

    Stops after MAX_ITERATIONS or MAX_HORIZON_YEARS past the anchor. Yields
    candidates only; COUNT/UNTIL/EXDATE are applied by the caller.
    """
    rule = parse_rrule(event.get("rrule"))
    if not rule.get("FREQ"):
        return
    try:
        interval = max(1, int(rule.get("INTERVAL", "1")))
    except ValueError:
        interval = 1
    freq = rule["FREQ"].upper()

    dtstart_raw = event.get("dtstart") or ""
    anchor = _parse_ical_dt(dtstart_raw)
    if anchor is None:
        return

    if freq == "DAILY":
        byday = rule.get("BYDAY", "")
        allowed = None
        if byday:
            allowed = {
                _WEEKDAY_CODES[t.strip().upper()]
                for t in byday.split(",")
                if t.strip().upper() in _WEEKDAY_CODES
            }
        step = timedelta(days=interval)
        cursor = anchor
        for _ in range(MAX_ITERATIONS):
            if allowed is None or cursor.weekday() in allowed:
                yield cursor
            cursor += step
    elif freq == "WEEKLY":
        byday = rule.get("BYDAY", "")
        wanted = []
        for token in byday.split(","):
            token = token.strip().upper()
            if token in _WEEKDAY_CODES:
                wanted.append(_WEEKDAY_CODES[token])
        if not wanted:
            wanted = [anchor.weekday()]
        wanted.sort()
        week_start = anchor - timedelta(days=anchor.weekday())
        for _ in range(MAX_ITERATIONS):
            for wd in wanted:
                candidate = week_start + timedelta(days=wd)
                if candidate >= anchor:
                    yield candidate
            week_start += timedelta(weeks=interval)
    elif freq == "MONTHLY":
        year, month = anchor.year, anchor.month
        for _ in range(MAX_ITERATIONS):
            for dom in _month_occurrences(year, month, rule, anchor):
                candidate = datetime(year, month, dom, anchor.hour, anchor.minute)
                if candidate >= anchor:
                    yield candidate
            month += interval
            year += (month - 1) // 12
            month = (month - 1) % 12 + 1
            if year > anchor.year + MAX_HORIZON_YEARS * 12:
                return
    elif freq == "YEARLY":
        for i in range(MAX_ITERATIONS):
            year = anchor.year + i * interval
            try:
                candidate = datetime(year, anchor.month, anchor.day, anchor.hour, anchor.minute)
            except ValueError:
                continue
            yield candidate
    else:
        yield anchor


def _event_span(event):
    """Return (start, end) naive datetimes for an event, or None if unparseable."""
    start = _parse_ical_dt(event.get("dtstart") or "")
    if start is None:
        return None
    end = _parse_ical_dt(event.get("dtend") or "")
    if end is None:
        end = start + (timedelta(days=1) if event.get("all_day") else timedelta(hours=1))
    return (start, end)


def _shift_occurrence(event, occurrence_start, duration):
    """Build an occurrence dict with dtstart/dtend shifted to occurrence_start."""
    occurrence_end = occurrence_start + duration
    occ = dict(event)
    if event.get("all_day"):
        occ["dtstart"] = occurrence_start.date().isoformat()
        occ["dtend"] = occurrence_end.date().isoformat()
    else:
        occ["dtstart"] = occurrence_start.isoformat()
        occ["dtend"] = occurrence_end.isoformat()
    occ["recurrence_date"] = occurrence_start.isoformat()
    occ["is_occurrence"] = True
    return occ


def expand_event(event, range_start, range_end, override_starts=None):
    """Expand one recurring event into occurrences intersecting the range.

    override_starts: set of minute-precision keys for RECURRENCE-ID overrides
    of this event's UID; generated occurrences matching an override are
    skipped (the override row itself is returned separately).
    """
    rule = parse_rrule(event.get("rrule"))
    span = _event_span(event)
    if span is None or not rule:
        return [event] if _event_intersects(event, range_start, range_end) else []
    duration = span[1] - span[0]

    bound_start = _parse_bound(range_start)
    bound_end = _parse_bound(range_end, end_of_day=True)
    horizon_limit = None
    if bound_end is not None:
        horizon_limit = bound_end + timedelta(days=366 * MAX_HORIZON_YEARS)

    until = _parse_ical_dt(rule.get("UNTIL", ""))
    try:
        count = int(rule.get("COUNT", "0")) or None
    except ValueError:
        count = None

    exkeys = _to_exdate_keys(_load_date_list(event.get("exdates")))
    rdates = [
        parsed
        for parsed in (_parse_ical_dt(v) for v in _load_date_list(event.get("rdates")))
        if parsed is not None
    ]

    override_keys = override_starts or set()
    generated = 0
    occurrences = []
    seen = set()

    def _add(candidate):
        nonlocal generated
        key = candidate.strftime("%Y%m%dT%H%M")
        if key in seen:
            return True
        if key in exkeys or key in override_keys:
            return True
        occ_start = candidate
        occ_end = candidate + duration
        if bound_start is not None and occ_end <= bound_start:
            return True
        if bound_end is not None and occ_start >= bound_end:
            return False
        seen.add(key)
        occurrences.append(_shift_occurrence(event, candidate, duration))
        return True

    for candidate in _iter_rrule_starts(event):
        if horizon_limit is not None and candidate > horizon_limit:
            break
        if until is not None and candidate > until:
            break
        generated += 1
        keep_going = _add(candidate)
        if count is not None and generated >= count:
            break
        if not keep_going:
            break

    for candidate in sorted(rdates):
        if until is not None and candidate > until:
            continue
        _add(candidate)

    return occurrences


def _intersects(start, end, range_start, range_end):
    bound_start = _parse_bound(range_start)
    bound_end = _parse_bound(range_end, end_of_day=True)
    if bound_start is not None and end <= bound_start:
        return False
    return not (bound_end is not None and start >= bound_end)


def _event_intersects(event, range_start, range_end):
    span = _event_span(event)
    if span is None:
        return False
    return _intersects(span[0], span[1], range_start, range_end)
    span = _event_span(event)
    if span is None:
        return False
    return _intersects(span[0], span[1], range_start, range_end)


def expand_events(events, range_start, range_end):
    """Expand a list of cached event dicts into occurrences for a range.

    Recurring masters are expanded (skipping occurrences that have
    RECURRENCE-ID override rows); non-recurring and override rows pass
    through unchanged when they intersect the range. Result is sorted by
    dtstart.
    """
    override_keys_by_uid = {}
    for event in events:
        recurrence_id = event.get("recurrence_id")
        if recurrence_id:
            parsed = _parse_ical_dt(recurrence_id)
            if parsed is not None:
                override_keys_by_uid.setdefault(event.get("uid"), set()).add(
                    parsed.strftime("%Y%m%dT%H%M")
                )

    result = []
    for event in events:
        if event.get("recurrence_id"):
            if _event_intersects(event, range_start, range_end):
                result.append(event)
            continue
        if event.get("rrule"):
            try:
                result.extend(
                    expand_event(
                        event,
                        range_start,
                        range_end,
                        override_keys_by_uid.get(event.get("uid")),
                    )
                )
            except Exception:
                logger.exception(
                    "recurrence expansion failed event_id=%s uid=%s",
                    event.get("id"),
                    event.get("uid"),
                )
                if _event_intersects(event, range_start, range_end):
                    result.append(event)
        else:
            if _event_intersects(event, range_start, range_end):
                result.append(event)

    def _sort_key(e):
        span = _event_span(e)
        return span[0] if span else datetime.min

    result.sort(key=_sort_key)
    return result
