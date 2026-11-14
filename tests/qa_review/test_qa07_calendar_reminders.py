"""ATTACK 7 (issues #10/#11): calendar reminders end-to-end.

- create with type case variants ("Display"/"email"/"PUSH"/"dIsPlAy"):
  valid TRIGGER durations in the ical, echoed back on get/list
- invalid types: loud 400 and NO CalDAV call
- update with reminders replaces the whole set
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.api.conftest import auth_header, cleanup_cache_db, create_api_token, setup_cache_db


@pytest.fixture()
def calendar_api(app, api_customer):
    from app.modules.calendar.services.cache import get_cache_path as _cal_path

    client, user_id, account_id = api_customer
    with app.app_context():
        token_value, _ = create_api_token(app, user_id)
    cache_path = setup_cache_db(app, account_id, cache_path_fn=_cal_path)
    yield client, token_value, cache_path
    cleanup_cache_db(cache_path)


def _seed_calendar(cache_path):
    from app.modules.calendar.services.cache_db import open_cache, upsert_calendar

    conn = open_cache(cache_path, "a" * 64)
    cal_id = upsert_calendar(
        conn,
        uid="cal-qa",
        href="/caldav/cal-qa/",
        displayname="Personal",
        color="#4285f4",
        is_default=True,
    )
    conn.close()
    return cal_id


def _caldav_env():
    """Mock CalDAV session + create/update; returns the patch objects."""
    mock_session = MagicMock()
    create_event = patch(
        "app.modules.calendar.services.caldav.create_event",
        return_value=("/caldav/cal-qa/new.ics", "etag-qa"),
    )
    update_event = patch(
        "app.modules.calendar.services.caldav.update_event", return_value="etag-qa2"
    )
    session_patch = patch(
        "app.api.controllers.calendar._get_caldav_session",
        return_value=(mock_session, None, None, None),
    )
    return session_patch, create_event, update_event


EVENT_BODY = {
    "summary": "QA event",
    "start": "2026-06-01T10:00:00+00:00",
    "end": "2026-06-01T11:00:00+00:00",
}


class TestCreateRemindersTypeVariants:
    @pytest.mark.parametrize(
        ("raw_type", "action"),
        [
            ("Display", "DISPLAY"),
            ("email", "EMAIL"),
            ("PUSH", "PUSH"),
            ("dIsPlAy", "DISPLAY"),
            (" notification", None),  # trailing space still invalid after strip
        ],
    )
    def test_create_case_variants(self, app, calendar_api, raw_type, action):
        client, token, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        session_patch, create_event, _ = _caldav_env()
        with session_patch, create_event as mock_create:
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    **EVENT_BODY,
                    "reminders": [{"type": raw_type, "trigger_minutes": 15}],
                },
                headers=auth_header(token),
            )
        if action is None:
            assert resp.status_code == 400, resp.data
            assert json.loads(resp.data)["error"]["code"] == "VALIDATION_ERROR"
            mock_create.assert_not_called()
            return
        assert resp.status_code == 201, resp.data
        ical_text = mock_create.call_args[0][2]
        assert "TRIGGER:-PT15M" in ical_text, ical_text
        assert f"ACTION:{action}" in ical_text, ical_text
        # echoed back on create response too
        assert json.loads(resp.data)["data"]["reminders"] == [
            {"type": action, "trigger_minutes": 15}
        ]

    @pytest.mark.parametrize("bad_type", ["notification", "popup", "sms", "Email2"])
    def test_invalid_types_never_touch_caldav(self, app, calendar_api, bad_type):
        client, token, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        session_patch, create_event, _ = _caldav_env()
        with session_patch, create_event as mock_create:
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    **EVENT_BODY,
                    "reminders": [{"type": bad_type, "trigger_minutes": 15}],
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 400, (bad_type, resp.data)
        assert json.loads(resp.data)["error"]["code"] == "VALIDATION_ERROR"
        mock_create.assert_not_called()

    def test_empty_type_defaults_to_display(self, app, calendar_api):
        """Documented lenient behavior: an explicitly empty type falls back
        to DISPLAY (reminders.py:61 'or \"DISPLAY\"'), it is not a 400."""
        client, token, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        session_patch, create_event, _ = _caldav_env()
        with session_patch, create_event:
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    **EVENT_BODY,
                    "reminders": [{"type": "", "trigger_minutes": 15}],
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201, resp.data
        assert json.loads(resp.data)["data"]["reminders"] == [
            {"type": "DISPLAY", "trigger_minutes": 15}
        ]

    def test_read_back_reminders_from_get_and_list(self, app, calendar_api):
        client, token, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        session_patch, create_event, _ = _caldav_env()
        with session_patch, create_event:
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    **EVENT_BODY,
                    "reminders": [
                        {"type": "Display", "trigger_minutes": 60},
                        {"type": "PUSH", "trigger_minutes": 0},
                    ],
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201, resp.data
        event_id = json.loads(resp.data)["data"]["id"]

        got = client.get(f"/api/v1/calendar/events/{event_id}", headers=auth_header(token))
        assert got.status_code == 200
        reminders = json.loads(got.data)["data"]["reminders"]
        assert {"type": "DISPLAY", "trigger_minutes": 60} in reminders
        assert {"type": "PUSH", "trigger_minutes": 0} in reminders

        listed = client.get(
            f"/api/v1/calendar/calendars/{cal_id}/events", headers=auth_header(token)
        )
        assert listed.status_code == 200
        target = next(e for e in json.loads(listed.data)["data"] if e.get("id") == event_id)
        assert len(target["reminders"]) == 2


class TestUpdateRemindersReplacesSet:
    def test_update_replaces_whole_alarm_set(self, app, calendar_api):
        client, token, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)

        # create with one alarm
        session_patch, create_event, _ = _caldav_env()
        with session_patch, create_event:
            resp = client.post(
                "/api/v1/calendar/events",
                json={
                    "calendar_id": cal_id,
                    **EVENT_BODY,
                    "reminders": [{"type": "DISPLAY", "trigger_minutes": 30}],
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 201, resp.data
        event_id = json.loads(resp.data)["data"]["id"]

        # update with a different set of two
        session_patch, _create, update_event = _caldav_env()
        with session_patch, update_event as mock_update:
            resp = client.put(
                f"/api/v1/calendar/events/{event_id}",
                json={
                    "reminders": [
                        {"type": "email", "trigger_minutes": 10},
                        {"type": "DISPLAY", "trigger_minutes": 120},
                    ]
                },
                headers=auth_header(token),
            )
        assert resp.status_code == 200, resp.data
        ical_text = mock_update.call_args[0][2]
        assert "TRIGGER:-PT10M" in ical_text
        assert "TRIGGER:-PT2H" in ical_text
        assert "ACTION:EMAIL" in ical_text
        # the old 30-minute alarm is gone
        assert "TRIGGER:-PT30M" not in ical_text

        got = client.get(f"/api/v1/calendar/events/{event_id}", headers=auth_header(token))
        reminders = json.loads(got.data)["data"]["reminders"]
        assert sorted(r["trigger_minutes"] for r in reminders) == [10, 120]

    def test_update_invalid_reminder_rejected_before_caldav(self, app, calendar_api):
        client, token, cache_path = calendar_api
        cal_id = _seed_calendar(cache_path)
        session_patch, create_event, _ = _caldav_env()
        with session_patch, create_event:
            resp = client.post(
                "/api/v1/calendar/events",
                json={**EVENT_BODY, "calendar_id": cal_id},
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        event_id = json.loads(resp.data)["data"]["id"]

        session_patch, _create, update_event = _caldav_env()
        with session_patch, update_event as mock_update:
            resp = client.put(
                f"/api/v1/calendar/events/{event_id}",
                json={"reminders": [{"type": "popup", "trigger_minutes": 5}]},
                headers=auth_header(token),
            )
        assert resp.status_code == 400
        assert json.loads(resp.data)["error"]["code"] == "VALIDATION_ERROR"
        mock_update.assert_not_called()
