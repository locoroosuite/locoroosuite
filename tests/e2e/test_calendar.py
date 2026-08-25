import re
import uuid
from datetime import date, timedelta

import pytest
import requests

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import (
    CALDAV_URL,
    E2E_DEFAULT_PASSWORD,
    caldav_get_calendars,
    caldav_get_events,
    wait_for,
)


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
                    caldav_get_events("e2e-test@test.localhost", c["href"]) for c in cal_home
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


E2E_USER = "e2e-test@test.localhost"

RICH_DESCRIPTION = (
    "Recruitment conversation with Gemma Agnew (a&co Recruitment Partners) re: "
    "Software Engineering Manager role in Adelaide.\n\n"
    "Teams join: https://teams.microsoft.com/meet/450750393784079?p=qqdbYs1PzmBEDZYrhK\n"
    "Meeting ID: 450 750 393 784 079\n"
    "Passcode: gK2Sn3Ys\n\n"
    "+61 425 275 210"
)


def _unfold_lines(text):
    out = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _fetch_event_ics(uid):
    """Find and GET the raw .ics for uid directly from the CalDAV server."""
    import xml.etree.ElementTree as ET

    r = requests.request(
        "PROPFIND",
        f"{CALDAV_URL}/{E2E_USER}/",
        auth=(E2E_USER, E2E_DEFAULT_PASSWORD),
        headers={"Depth": "1", "Content-Type": "application/xml"},
        timeout=5,
    )
    if r.status_code not in (200, 207):
        return None
    root = ET.fromstring(r.text)
    for resp_elem in root.findall("{DAV:}response"):
        href_el = resp_elem.find("{DAV:}href")
        href = (href_el.text or "") if href_el is not None else ""
        if not href.endswith("/"):
            continue
        if href.rstrip("/").rsplit("/", 1)[-1] == E2E_USER:
            continue
        ics_r = requests.get(
            f"{CALDAV_URL}{href}{uid}.ics",
            auth=(E2E_USER, E2E_DEFAULT_PASSWORD),
            timeout=5,
        )
        if ics_r.status_code == 200 and "BEGIN:VEVENT" in ics_r.text:
            return ics_r.text
    return None


@skip_if_no_services
class TestCalendarApiRichDescription:
    def test_create_and_update_event_with_rich_description(self, app_url, user_session):
        # Enable API access and create a token with calendar scopes
        r = user_session.get(f"{app_url}/app/mail/settings/api", allow_redirects=True)
        if "Enable API Access" in r.text or "enable" in r.text.lower():
            user_session.post(
                f"{app_url}/app/mail/settings/api/enable",
                data={"password": E2E_DEFAULT_PASSWORD},
                allow_redirects=True,
            )
        tag = uuid.uuid4().hex[:8]
        r = user_session.post(
            f"{app_url}/app/mail/settings/api/tokens/create",
            data={
                "token_name": f"E2E Cal Rich {tag}",
                "scope_calendar_read": "on",
                "scope_calendar_write": "on",
            },
            allow_redirects=True,
        )
        assert r.status_code == 200
        token_match = re.search(r"font-mono[^>]*>([^<]+)</div>", r.text)
        assert token_match, "API token not found in response"
        headers = {"Authorization": f"Bearer {token_match.group(1).strip()}"}

        resp = requests.get(f"{app_url}/api/v1/calendar/calendars", headers=headers, timeout=10)
        assert resp.status_code == 200
        calendars = resp.json()["data"]
        assert calendars, "No calendars in cache"
        calendar_id = calendars[0]["id"]

        # Create with a multi-line description containing a URL, & and +
        resp = requests.post(
            f"{app_url}/api/v1/calendar/events",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "calendar_id": calendar_id,
                "summary": f"E2E Rich Description {tag}",
                "start": "2026-09-01T10:00:00+00:00",
                "end": "2026-09-01T11:00:00+00:00",
                "location": "Microsoft Teams",
                "description": RICH_DESCRIPTION,
            },
            timeout=15,
        )
        assert resp.status_code == 201, resp.text
        event = resp.json()["data"]
        event_id = event["id"]
        uid = event["uid"]

        try:
            # Update another field first — succeeds and refreshes the etag
            resp = requests.put(
                f"{app_url}/api/v1/calendar/events/{event_id}",
                headers={**headers, "Content-Type": "application/json"},
                json={"location": "Room 202"},
                timeout=15,
            )
            assert resp.status_code == 200, resp.text

            # Description-only update after a prior update — the 412 regression
            resp = requests.put(
                f"{app_url}/api/v1/calendar/events/{event_id}",
                headers={**headers, "Content-Type": "application/json"},
                json={"description": RICH_DESCRIPTION + "\nUpdated via API"},
                timeout=15,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["description"] == RICH_DESCRIPTION + "\nUpdated via API"

            # Verify the stored resource on the CalDAV server round-trips
            ics = wait_for(lambda: _fetch_event_ics(uid), timeout=10)
            assert ics, "event .ics not found on CalDAV server"
            for line in ics.splitlines():
                assert len(line.encode("utf-8")) <= 75, line
            desc_lines = [ln for ln in _unfold_lines(ics) if ln.startswith("DESCRIPTION:")]
            assert len(desc_lines) == 1
            desc_value = desc_lines[0][len("DESCRIPTION:") :]
            assert "\\n" in desc_value
            assert (
                "https://teams.microsoft.com/meet/450750393784079"
                "?p=qqdbYs1PzmBEDZYrhK" in desc_value
            )
            assert "Updated via API" in desc_value
        finally:
            requests.delete(
                f"{app_url}/api/v1/calendar/events/{event_id}",
                headers=headers,
                timeout=15,
            )
