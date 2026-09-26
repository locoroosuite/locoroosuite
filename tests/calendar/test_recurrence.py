"""Unit tests for server-side recurrence expansion (U12.56f)."""

from app.modules.calendar.services.recurrence import (
    expand_event,
    expand_events,
    parse_rrule,
)


def _event(**overrides):
    base = {
        "id": 1,
        "uid": "evt-1",
        "summary": "Standup",
        "dtstart": "2026-01-05T09:00:00",  # Monday
        "dtend": "2026-01-05T09:30:00",
        "all_day": 0,
        "rrule": "",
        "calendar_id": 1,
    }
    base.update(overrides)
    return base


def test_parse_rrule_parts():
    rule = parse_rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=10")
    assert rule["FREQ"] == "WEEKLY"
    assert rule["INTERVAL"] == "2"
    assert rule["BYDAY"] == "MO,WE"
    assert rule["COUNT"] == "10"


def test_non_recurring_event_passes_through():
    events = [_event()]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    assert len(result) == 1
    assert result[0]["dtstart"] == "2026-01-05T09:00:00"
    assert not result[0].get("is_occurrence")


def test_weekly_expansion_within_range():
    events = [_event(rrule="FREQ=WEEKLY")]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    # Mondays in January 2026: 5, 12, 19, 26
    starts = [occ["dtstart"] for occ in result]
    assert starts == [
        "2026-01-05T09:00:00",
        "2026-01-12T09:00:00",
        "2026-01-19T09:00:00",
        "2026-01-26T09:00:00",
    ]
    assert all(occ["is_occurrence"] for occ in result)
    assert all(occ["dtend"].endswith("T09:30:00") for occ in result)


def test_daily_expansion_with_count():
    events = [_event(rrule="FREQ=DAILY;COUNT=3")]
    result = expand_events(events, "2026-01-01", "2026-12-31")
    assert len(result) == 3
    assert result[2]["dtstart"] == "2026-01-07T09:00:00"


def test_daily_byday_filter():
    # Weekdays only (MO-FR), anchored Monday Jan 5 — 10 weekdays in range
    events = [_event(rrule="FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR")]
    result = expand_events(events, "2026-01-01", "2026-01-18")
    starts = [occ["dtstart"] for occ in result]
    assert "2026-01-05T09:00:00" in starts
    assert "2026-01-10T09:00:00" not in starts  # Saturday
    assert "2026-01-11T09:00:00" not in starts  # Sunday
    assert "2026-01-16T09:00:00" in starts


def test_weekly_byday_multiple_days():
    events = [_event(rrule="FREQ=WEEKLY;BYDAY=MO,WE")]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    starts = [occ["dtstart"] for occ in result]
    assert starts == [
        "2026-01-05T09:00:00",
        "2026-01-07T09:00:00",
        "2026-01-12T09:00:00",
        "2026-01-14T09:00:00",
        "2026-01-19T09:00:00",
        "2026-01-21T09:00:00",
        "2026-01-26T09:00:00",
        "2026-01-28T09:00:00",
    ]


def test_biweekly_interval():
    events = [_event(rrule="FREQ=WEEKLY;INTERVAL=2")]
    result = expand_events(events, "2026-01-01", "2026-02-28")
    starts = [occ["dtstart"] for occ in result]
    assert starts == [
        "2026-01-05T09:00:00",
        "2026-01-19T09:00:00",
        "2026-02-02T09:00:00",
        "2026-02-16T09:00:00",
    ]


def test_monthly_byday_with_bysetpos():
    # 2nd Tuesday of each month
    events = [_event(dtstart="2026-01-13T10:00:00", dtend="2026-01-13T11:00:00",
                     rrule="FREQ=MONTHLY;BYDAY=TU;BYSETPOS=2")]
    result = expand_events(events, "2026-01-01", "2026-04-30")
    starts = [occ["dtstart"] for occ in result]
    assert starts == [
        "2026-01-13T10:00:00",
        "2026-02-10T10:00:00",
        "2026-03-10T10:00:00",
        "2026-04-14T10:00:00",
    ]


def test_monthly_by_monthday_negative():
    # Last day of each month
    events = [_event(dtstart="2026-01-31T00:00:00", dtend="2026-02-01T00:00:00",
                     rrule="FREQ=MONTHLY;BYMONTHDAY=-1")]
    result = expand_events(events, "2026-01-01", "2026-04-30")
    starts = [occ["dtstart"][:10] for occ in result]
    assert starts == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]


def test_yearly_expansion():
    events = [_event(dtstart="2026-03-01T09:00:00", dtend="2026-03-01T10:00:00",
                     rrule="FREQ=YEARLY")]
    result = expand_events(events, "2027-01-01", "2028-12-31")
    starts = [occ["dtstart"] for occ in result]
    assert starts == ["2027-03-01T09:00:00", "2028-03-01T09:00:00"]


def test_exdate_removes_occurrence():
    events = [_event(rrule="FREQ=WEEKLY", exdates='["2026-01-12T09:00:00"]')]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    starts = [occ["dtstart"] for occ in result]
    assert "2026-01-12T09:00:00" not in starts
    assert "2026-01-05T09:00:00" in starts


def test_rdate_adds_occurrence():
    events = [_event(rrule="FREQ=WEEKLY", rdates='["2026-01-08T09:00:00"]')]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    starts = [occ["dtstart"] for occ in result]
    assert "2026-01-08T09:00:00" in starts


def test_until_limits_series():
    events = [_event(rrule="FREQ=WEEKLY;UNTIL=20260113T000000Z")]
    result = expand_events(events, "2026-01-01", "2026-03-31")
    starts = [occ["dtstart"] for occ in result]
    assert starts == ["2026-01-05T09:00:00", "2026-01-12T09:00:00"]


def test_override_replaces_generated_occurrence():
    master = _event(id=1, rrule="FREQ=WEEKLY")
    override = _event(
        id=2,
        uid="evt-1",
        summary="Standup (moved)",
        dtstart="2026-01-14T15:00:00",
        dtend="2026-01-14T15:30:00",
        recurrence_id="2026-01-12T09:00:00",
        rrule="",
    )
    result = expand_events([master, override], "2026-01-01", "2026-01-31")
    starts = [(occ["id"], occ["dtstart"]) for occ in result]
    # Generated Mon Jan 12 occurrence replaced by the override row
    assert (2, "2026-01-14T15:00:00") in starts
    assert (1, "2026-01-12T09:00:00") not in starts
    assert (1, "2026-01-05T09:00:00") in starts
    assert (1, "2026-01-19T09:00:00") in starts


def test_all_day_series_expansion():
    events = [
        _event(
            dtstart="2026-01-05",
            dtend="2026-01-05",
            all_day=1,
            rrule="FREQ=DAILY;COUNT=3",
        )
    ]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    assert [occ["dtstart"] for occ in result] == ["2026-01-05", "2026-01-06", "2026-01-07"]


def test_occurrences_preserve_duration():
    events = [_event(dtstart="2026-01-05T09:00:00", dtend="2026-01-05T10:30:00",
                     rrule="FREQ=WEEKLY")]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    for occ in result:
        assert occ["dtend"].endswith("T10:30:00")


def test_range_before_series_start_yields_nothing():
    events = [_event(rrule="FREQ=WEEKLY")]
    result = expand_events(events, "2025-01-01", "2025-12-31")
    assert result == []


def test_master_outside_range_window_still_expands():
    """The range SQL misses old masters; expansion covers them (U12.56f)."""
    events = [_event(rrule="FREQ=WEEKLY")]  # started 2026-01-05
    result = expand_event(events[0], "2026-06-01", "2026-06-30")
    starts = [occ["dtstart"] for occ in result]
    assert starts == [
        "2026-06-01T09:00:00",
        "2026-06-08T09:00:00",
        "2026-06-15T09:00:00",
        "2026-06-22T09:00:00",
        "2026-06-29T09:00:00",
    ]


def test_occurrence_identity_is_stable():
    events = [_event(rrule="FREQ=WEEKLY")]
    result = expand_events(events, "2026-01-01", "2026-01-31")
    for occ in result:
        assert occ["recurrence_date"] == occ["dtstart"]
        assert occ["id"] == 1
        assert occ["uid"] == "evt-1"
