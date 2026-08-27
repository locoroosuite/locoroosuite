"""Resolve iCalendar TZIDs (IANA, Microsoft/Exchange, custom) to tzinfo objects.

Resolution layers, in order:
1. Direct IANA lookup via ZoneInfo (also tolerates a leading "/").
2. Microsoft/Exchange Windows TZID map (see windows_tzid_map.py).
3. Computation from the event's embedded VTIMEZONE definition (fixed offset
   for the given instant, honouring STANDARD/DAYLIGHT transitions).

Callers must handle a None return by logging a warning and falling back to
UTC — never silently swallow an unresolvable TZID.
"""

import logging
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.shared.windows_tzid_map import WINDOWS_TZID_TO_IANA

logger = logging.getLogger(__name__)

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_BYDAY_RE = re.compile(r"^([+-]?\d{1,2})([A-Z]{2})$")
_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def resolve_tzid(tzid, vtimezones=None, dt=None) -> tzinfo | None:
    """Resolve a TZID to a tzinfo.

    Args:
        tzid: raw TZID string from the iCalendar data (may be None/empty).
        vtimezones: parsed VTIMEZONE definitions as produced by
            app.shared.icalendar.parse_icalendar (dict: tzid -> list of
            observance dicts), or None when not available.
        dt: naive datetime in the event's zone, used to pick the correct
            STANDARD/DAYLIGHT offset (only relevant for layer 3).

    Returns:
        A tzinfo, or None when the TZID cannot be resolved by any layer.
    """
    if not tzid:
        return None
    raw = tzid.strip().strip('"')
    for candidate in dict.fromkeys((raw, raw.lstrip("/"))):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    mapped = WINDOWS_TZID_TO_IANA.get(raw)
    if mapped:
        try:
            return ZoneInfo(mapped)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("windows tzid mapped to unknown IANA zone: %r -> %r", raw, mapped)
    if vtimezones:
        observances = vtimezones.get(raw)
        if observances is None:
            wanted = raw.lower()
            for key, obs in vtimezones.items():
                if (key or "").strip().strip('"').lower() == wanted:
                    observances = obs
                    break
        if observances:
            tz = _offset_from_observances(raw, observances, dt)
            if tz is not None:
                return tz
    return None


def resolve_tzid_name(tzid, vtimezones=None) -> str | None:
    """Resolve a TZID to an IANA zone name where possible.

    Returns the IANA name (e.g. "Australia/Adelaide") for IANA and
    Windows-mapped zones, the original TZID for VTIMEZONE-computed fixed
    offsets, and None when unresolvable.
    """
    tz = resolve_tzid(tzid, vtimezones=vtimezones)
    if tz is None:
        return None
    return getattr(tz, "key", None) or tzid


def _offset_from_observances(name, observances, dt) -> tzinfo | None:
    parsed = []
    for obs in observances:
        offset = _parse_utc_offset(obs.get("tzoffsetto"))
        if offset is not None:
            parsed.append((obs, offset))
    if not parsed:
        return None
    if len(parsed) == 1 or dt is None:
        chosen = parsed[0]
        for obs, offset in parsed:
            if obs.get("type") == "STANDARD":
                chosen = (obs, offset)
        return timezone(chosen[1], name=name)
    transitions = []
    for obs, offset in parsed:
        for local in _transition_datetimes(obs, dt.year):
            transitions.append((local, offset))
    if not transitions:
        return timezone(parsed[0][1], name=name)
    transitions.sort(key=lambda item: item[0])
    chosen_offset = transitions[-1][1]
    for local, offset in transitions:
        if local <= dt:
            chosen_offset = offset
    return timezone(chosen_offset, name=name)


def _transition_datetimes(obs, year):
    times = []
    base = _parse_vtime_dtstart(obs.get("dtstart"))
    rrule = _parse_rrule(obs.get("rrule"))
    if base and rrule and rrule.get("freq") == "YEARLY":
        bymonth = rrule.get("bymonth")
        byday = rrule.get("byday")
        interval = rrule.get("interval") or 1
        base_date, at = base
        base_year = base_date.year if base_date.year > 1601 else year
        if bymonth and byday and (interval <= 1 or (year - base_year) % interval == 0):
            day = _nth_weekday(year, bymonth, byday)
            if day is not None:
                times.append(datetime.combine(day, at))
    elif base:
        base_date, at = base
        times.append(datetime.combine(date(year, base_date.month, base_date.day), at))
    for rdate in obs.get("rdates") or []:
        parsed_rdate = _parse_basic_dt(rdate)
        if parsed_rdate is not None:
            times.append(parsed_rdate)
    return times


def _parse_vtime_dtstart(value):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except (ValueError, TypeError):
        return None
    return dt.date(), dt.time()


def _parse_basic_dt(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S")
    except (ValueError, TypeError):
        return None


def _parse_rrule(value):
    if not value:
        return None
    rule = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        key_upper = key.strip().upper()
        val = val.strip()
        if key_upper == "FREQ":
            rule["freq"] = val.upper()
        elif key_upper == "INTERVAL":
            try:
                rule["interval"] = int(val)
            except ValueError:
                continue
        elif key_upper == "BYMONTH":
            try:
                rule["bymonth"] = int(val)
            except ValueError:
                continue
        elif key_upper == "BYDAY":
            rule["byday"] = val.upper()
    return rule


def _nth_weekday(year, month, byday) -> date | None:
    match = _BYDAY_RE.match(byday or "")
    if not match:
        return None
    n = int(match.group(1))
    weekday = _WEEKDAYS.get(match.group(2))
    if weekday is None or n == 0:
        return None
    if n > 0:
        first = date(year, month, 1)
        day = 1 + (weekday - first.weekday()) % 7 + (n - 1) * 7
    else:
        last_day = monthrange(year, month)[1]
        last = date(year, month, last_day)
        day = last_day - (last.weekday() - weekday) % 7 + (n + 1) * 7
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_utc_offset(value) -> timedelta | None:
    if not value:
        return None
    match = _OFFSET_RE.match(value.strip())
    if not match:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if hours > 23 or minutes > 59:
        return None
    return sign * timedelta(hours=hours, minutes=minutes)
