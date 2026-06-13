import re
import uuid
from datetime import date, timedelta

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import caldav_get_calendars, caldav_get_events, wait_for


@skip_if_no_services
class TestCalendarIndex:
    def test_calendar_index_loads(self, app_url, user_session):
        r = user_session.get(f"{app_url}/app/calendar/", allow_redirects=True)
        assert r.status_code == 200

    def test_sidebar_shows_calendars(self, app_url, user_session):
        r = user_session.get(f"{app_url}/app/calendar/", allow_redirects=True)
        assert r.status_code == 200
        calendar_ids = re.findall(r"/calendar/calendars/(\d+)/toggle", r.text)
        assert len(calendar_ids) >= 1


@skip_if_no_services
class TestCalendarEvents:
    def test_create_edit_delete_event(self, app_url, user_session):
        r = user_session.get(f"{app_url}/app/calendar/", allow_redirects=True)
        assert r.status_code == 200
        calendar_ids = re.findall(r"/calendar/calendars/(\d+)/toggle", r.text)
        if not calendar_ids:
            pytest.skip("No calendars available")
        calendar_id = calendar_ids[0]

        tag = uuid.uuid4().hex[:8]
        summary = f"E2E Event {tag}"
        tomorrow = date.today() + timedelta(days=1)
        next_week = tomorrow + timedelta(days=7)

        r = user_session.post(
            f"{app_url}/app/calendar/events/new",
            data={
                "summary": summary,
                "dtstart_date": tomorrow.isoformat(),
                "dtstart_time": "10:00",
                "dtend_date": tomorrow.isoformat(),
                "dtend_time": "11:00",
                "calendar_id": calendar_id,
                "timezone": "UTC",
            },
            allow_redirects=True,
        )
        assert r.status_code == 200

        r = user_session.get(
            f"{app_url}/app/calendar/api/events",
            params={
                "start": tomorrow.isoformat(),
                "end": next_week.isoformat(),
            },
        )
        assert r.status_code == 200
        events = r.json()
        matching = [e for e in events if e.get("summary") == summary]
        assert len(matching) >= 1
        event_id = matching[0]["id"]

        cal_home = caldav_get_calendars("e2e-test@test.localhost")
        if cal_home:
            wait_for(
                lambda: any(
                    caldav_get_events("e2e-test@test.localhost", c["href"])
                    for c in cal_home
                ),
                timeout=10,
            )

        edited_summary = f"E2E Edited {tag}"
        r = user_session.post(
            f"{app_url}/app/calendar/events/{event_id}/edit",
            data={
                "summary": edited_summary,
                "dtstart_date": tomorrow.isoformat(),
                "dtstart_time": "10:00",
                "dtend_date": tomorrow.isoformat(),
                "dtend_time": "11:00",
                "calendar_id": calendar_id,
                "timezone": "UTC",
            },
            allow_redirects=True,
        )
        assert r.status_code == 200

        r = user_session.get(
            f"{app_url}/app/calendar/api/events",
            params={
                "start": tomorrow.isoformat(),
                "end": next_week.isoformat(),
            },
        )
        assert r.status_code == 200
        events = r.json()
        edited_matching = [e for e in events if e.get("summary") == edited_summary]
        assert len(edited_matching) >= 1

        r = user_session.post(
            f"{app_url}/app/calendar/events/{event_id}/delete",
            allow_redirects=True,
        )
        assert r.status_code == 200

        r = user_session.get(
            f"{app_url}/app/calendar/api/events",
            params={
                "start": tomorrow.isoformat(),
                "end": next_week.isoformat(),
            },
        )
        assert r.status_code == 200
        events = r.json()
        assert not any(e.get("summary") == edited_summary for e in events)
