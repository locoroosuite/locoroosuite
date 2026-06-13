import logging
import uuid
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

DAV_NS = "DAV:"
CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
CALSCALE_NS = "urn:ietf:params:xml:ns:caldav"

_ns = {"d": DAV_NS, "c": CALDAV_NS}

_PROPFIND_PRINCIPAL = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:resourcetype />
    <d:displayname />
    <d:getetag />
    <d:sync-token />
  </d:prop>
</d:propfind>"""

_PROPFIND_CALENDAR = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:resourcetype />
    <d:displayname />
    <d:getetag />
    <c:calendar-color />
    <c:calendar-description />
    <d:sync-token />
  </d:prop>
</d:propfind>"""

_CALENDAR_QUERY = """<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag />
    <c:calendar-data />
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT" />
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

_CALENDAR_QUERY_RANGE = """<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag />
    <c:calendar-data />
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start}" end="{end}" />
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""


def _make_session(username, password):
    s = requests.Session()
    if password:
        s.auth = (username, password)
    s.headers.update({
        "User-Agent": "LocoRooSuite/1.0",
        "X-Remote-User": username,
    })
    return s


def discover_calendars(base_url, username, password):
    s = _make_session(username, password)
    principal_url = f"{base_url.rstrip('/')}/{username}/"
    resp = s.request(
        "PROPFIND",
        principal_url,
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
        data=_PROPFIND_PRINCIPAL,
        timeout=15,
    )
    resp.raise_for_status()
    calendars = []
    root = ET.fromstring(resp.content)
    for resp_elem in root.findall(f".//{{{DAV_NS}}}response"):
        href_elem = resp_elem.find(f"{{{DAV_NS}}}href")
        rt_elem = resp_elem.find(f".//{{{DAV_NS}}}resourcetype")
        if rt_elem is not None and rt_elem.find(f"{{{CALDAV_NS}}}calendar") is not None:
            cal_href = href_elem.text if href_elem is not None else None
            if cal_href:
                if not cal_href.startswith("http"):
                    cal_url = f"{base_url.rstrip('/')}{cal_href if cal_href.startswith('/') else '/' + cal_href}"
                else:
                    cal_url = cal_href
                displayname_elem = resp_elem.find(f".//{{{DAV_NS}}}displayname")
                displayname = displayname_elem.text if displayname_elem is not None else "Calendar"
                color_elem = resp_elem.find(f".//{{{CALDAV_NS}}}calendar-color")
                color = color_elem.text if color_elem is not None else "#4285f4"
                sync_token_elem = resp_elem.find(f".//{{{DAV_NS}}}sync-token")
                sync_token = sync_token_elem.text if sync_token_elem is not None else None
                calendars.append({
                    "url": cal_url,
                    "displayname": displayname,
                    "color": color,
                    "sync_token": sync_token,
                })
    return s, calendars


def create_calendar(session, base_url, username, name="Calendar", color="#4285f4"):
    cal_url = f"{base_url.rstrip('/')}/{username}/{name.lower().replace(' ', '-')}/"
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<d:mkcol xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:set>
    <d:prop>
      <d:resourcetype>
        <d:collection />
        <c:calendar />
      </d:resourcetype>
      <d:displayname>{name}</d:displayname>
      <c:calendar-color>{color}</c:calendar-color>
      <c:supported-calendar-component-set>
        <c:comp name="VEVENT" />
      </c:supported-calendar-component-set>
    </d:prop>
  </d:set>
</d:mkcol>"""
    resp = session.request(
        "MKCOL",
        cal_url,
        headers={"Content-Type": "application/xml; charset=utf-8"},
        data=body,
        timeout=15,
    )
    if resp.status_code in (201, 405):
        return cal_url
    resp.raise_for_status()
    return cal_url


def list_events(session, calendar_url):
    resp = session.request(
        "REPORT",
        calendar_url,
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
        data=_CALENDAR_QUERY,
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    events = []
    for resp_elem in root.findall(f".//{{{DAV_NS}}}response"):
        href_elem = resp_elem.find(f"{{{DAV_NS}}}href")
        etag_elem = resp_elem.find(f".//{{{DAV_NS}}}getetag")
        data_elem = resp_elem.find(f".//{{{CALDAV_NS}}}calendar-data")
        href = href_elem.text if href_elem is not None else ""
        etag = etag_elem.text if etag_elem is not None else ""
        ical_text = data_elem.text if data_elem is not None else ""
        if href and ical_text:
            events.append((href, etag, ical_text))
    return events


def get_event(session, href):
    resp = session.get(href, timeout=15)
    resp.raise_for_status()
    return resp.text


def create_event(session, calendar_url, ical_text, uid=None):
    if not uid:
        uid = str(uuid.uuid4())
    href = f"{calendar_url.rstrip('/')}/{uid}.ics"
    resp = session.put(
        href,
        data=ical_text.encode("utf-8"),
        headers={"Content-Type": "text/calendar; charset=utf-8"},
        timeout=15,
    )
    resp.raise_for_status()
    etag = resp.headers.get("ETag", "")
    return href, etag


def update_event(session, href, ical_text, etag=None):
    headers = {"Content-Type": "text/calendar; charset=utf-8"}
    if etag:
        headers["If-Match"] = etag
    resp = session.put(href, data=ical_text.encode("utf-8"), headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.headers.get("ETag", "")


def delete_event(session, href, etag=None):
    headers = {}
    if etag:
        headers["If-Match"] = etag
    resp = session.delete(href, headers=headers, timeout=15)
    if resp.status_code == 404:
        return True
    resp.raise_for_status()
    return True


def delete_calendar(session, calendar_url):
    resp = session.delete(calendar_url, timeout=15)
    if resp.status_code == 404:
        return True
    resp.raise_for_status()
    return True


def update_calendar_props(session, calendar_url, displayname=None, color=None):
    props = ""
    if displayname is not None:
        props += f"<d:displayname>{displayname}</d:displayname>"
    if color is not None:
        props += f"<c:calendar-color>{color}</c:calendar-color>"
    if not props:
        return
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<d:propertyupdate xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:set>
    <d:prop>{props}</d:prop>
  </d:set>
</d:propertyupdate>"""
    resp = session.request(
        "PROPPATCH",
        calendar_url,
        headers={"Content-Type": "application/xml; charset=utf-8"},
        data=body,
        timeout=15,
    )
    resp.raise_for_status()
