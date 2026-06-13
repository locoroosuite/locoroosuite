import logging
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

DAV_NS = "DAV:"
CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"

_ns = {"d": DAV_NS, "c": CARDDAV_NS}

_PROPFIND_RESOURCETYPE = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:resourcetype />
    <d:displayname />
    <d:getetag />
  </d:prop>
</d:propfind>"""

_ADDRESSBOOK_QUERY = """<?xml version="1.0" encoding="UTF-8"?>
<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:getetag />
    <c:address-data />
  </d:prop>
</c:addressbook-query>"""


def _make_session(username, password):
    s = requests.Session()
    if password:
        s.auth = (username, password)
    s.headers.update({
        "User-Agent": "LocoRooSuite/1.0",
        "X-Remote-User": username,
    })
    return s


def discover_address_book(base_url, username, password):
    s = _make_session(username, password)
    principal_url = f"{base_url.rstrip('/')}/{username}/"
    resp = s.request(
        "PROPFIND",
        principal_url,
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
        data=_PROPFIND_RESOURCETYPE,
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    for resp_elem in root.findall(f".//{{{DAV_NS}}}response"):
        href_elem = resp_elem.find(f"{{{DAV_NS}}}href")
        rt_elem = resp_elem.find(f".//{{{DAV_NS}}}resourcetype")
        if rt_elem is not None and rt_elem.find(f"{{{CARDDAV_NS}}}addressbook") is not None:
            abook_href = href_elem.text if href_elem is not None else None
            if abook_href:
                if not abook_href.startswith("http"):
                    abook_url = f"{base_url.rstrip('/')}{abook_href if abook_href.startswith('/') else '/' + abook_href}"
                else:
                    abook_url = abook_href
                displayname_elem = resp_elem.find(f".//{{{DAV_NS}}}displayname")
                displayname = displayname_elem.text if displayname_elem is not None else "Contacts"
                return s, abook_url, displayname
    return s, None, None


def create_address_book(session, base_url, username):
    abook_url = f"{base_url.rstrip('/')}/{username}/contacts/"
    body = """<?xml version="1.0" encoding="UTF-8"?>
<d:mkcol xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:set>
    <d:prop>
      <d:resourcetype>
        <d:collection />
        <c:addressbook />
      </d:resourcetype>
      <d:displayname>Contacts</d:displayname>
    </d:prop>
  </d:set>
</d:mkcol>"""
    resp = session.request(
        "MKCOL",
        abook_url,
        headers={"Content-Type": "application/xml; charset=utf-8"},
        data=body,
        timeout=15,
    )
    if resp.status_code in (201, 405):
        return abook_url
    resp.raise_for_status()
    return abook_url


def _resolve_href(address_book_url, href):
    if not href or href.startswith("http"):
        return href
    return urljoin(address_book_url, href)


def list_contacts(session, address_book_url):
    resp = session.request(
        "REPORT",
        address_book_url,
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
        data=_ADDRESSBOOK_QUERY,
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    contacts = []
    for resp_elem in root.findall(f".//{{{DAV_NS}}}response"):
        href_elem = resp_elem.find(f"{{{DAV_NS}}}href")
        etag_elem = resp_elem.find(f".//{{{DAV_NS}}}getetag")
        data_elem = resp_elem.find(f".//{{{CARDDAV_NS}}}address-data")
        href = href_elem.text if href_elem is not None else ""
        etag = etag_elem.text if etag_elem is not None else ""
        vcard_text = data_elem.text if data_elem is not None else ""
        if href and vcard_text:
            href = _resolve_href(address_book_url, href)
            contacts.append((href, etag, vcard_text))
    return contacts


def get_contact(session, href):
    resp = session.get(href, timeout=15)
    resp.raise_for_status()
    return resp.text


def create_contact(session, address_book_url, vcard_text, uid=None):
    if not uid:
        uid = str(uuid.uuid4())
    href = f"{address_book_url.rstrip('/')}/{uid}.vcf"
    resp = session.put(
        href,
        data=vcard_text.encode("utf-8"),
        headers={"Content-Type": "text/vcard; charset=utf-8"},
        timeout=15,
    )
    resp.raise_for_status()
    etag = resp.headers.get("ETag", "")
    return href, etag


def update_contact(session, href, vcard_text, etag=None):
    headers = {"Content-Type": "text/vcard; charset=utf-8"}
    if etag:
        headers["If-Match"] = etag
    resp = session.put(href, data=vcard_text.encode("utf-8"), headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.headers.get("ETag", "")


def delete_contact(session, href, etag=None):
    headers = {}
    if etag:
        headers["If-Match"] = etag
    resp = session.delete(href, headers=headers, timeout=15)
    if resp.status_code == 404:
        return True
    resp.raise_for_status()
    return True
