from unittest.mock import MagicMock



def _make_xml_response(href, etag, vcard_text):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<multistatus xmlns="DAV:">
  <response>
    <href>{href}</href>
    <propstat><prop>
      <getetag>{etag}</getetag>
    </prop></propstat>
  </response>
</multistatus>""".encode()


def _make_report_response(contacts):
    items = []
    for href, etag, vcard in contacts:
        items.append(f"""<d:response>
      <d:href>{href}</d:href>
      <d:propstat><d:prop>
        <d:getetag>{etag}</d:getetag>
        <c:address-data>{vcard}</c:address-data>
      </d:prop></d:propstat>
    </d:response>""")
    inner = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
{inner}
</d:multistatus>""".encode()


class TestResolveHref:
    def test_absolute_url_passed_through(self):
        from app.modules.contacts.services.carddav import _resolve_href
        abook = "http://localhost:5232/user/contacts/"
        href = "http://localhost:5232/user/contacts/abc.vcf"
        assert _resolve_href(abook, href) == href

    def test_relative_path_resolved(self):
        from app.modules.contacts.services.carddav import _resolve_href
        abook = "http://localhost:5232/user/contacts/"
        href = "/user/contacts/abc.vcf"
        assert _resolve_href(abook, href) == "http://localhost:5232/user/contacts/abc.vcf"

    def test_url_encoded_relative_path_resolved(self):
        from app.modules.contacts.services.carddav import _resolve_href
        abook = "http://localhost:5232/test%40test.localhost/contacts/"
        href = "/test%40test.localhost/contacts/abc.vcf"
        assert _resolve_href(abook, href) == "http://localhost:5232/test%40test.localhost/contacts/abc.vcf"

    def test_none_returns_none(self):
        from app.modules.contacts.services.carddav import _resolve_href
        assert _resolve_href("http://x/", None) is None

    def test_empty_returns_empty(self):
        from app.modules.contacts.services.carddav import _resolve_href
        assert _resolve_href("http://x/", "") == ""


class TestListContactsHrefResolution:
    def test_relative_hrefs_converted_to_full_urls(self):
        from app.modules.contacts.services.carddav import list_contacts

        abook_url = "http://localhost:5232/test%40test.localhost/contacts/"
        vcard = "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:abc-123\r\nFN:Test\r\nEND:VCARD\r\n"
        xml_body = _make_report_response([
            ("/test%40test.localhost/contacts/abc-123.vcf", '"etag-1"', vcard),
        ])

        session = MagicMock()
        resp = MagicMock()
        resp.content = xml_body
        resp.raise_for_status = MagicMock()
        session.request.return_value = resp

        contacts = list_contacts(session, abook_url)
        assert len(contacts) == 1
        href, etag, vcard_text = contacts[0]
        assert href.startswith("http://")
        assert "abc-123.vcf" in href
        assert "/test%40test.localhost/contacts/abc-123.vcf" not in href or href.startswith("http")

    def test_absolute_hrefs_preserved(self):
        from app.modules.contacts.services.carddav import list_contacts

        abook_url = "http://localhost:5232/user/contacts/"
        full_href = "http://other-host:5232/user/contacts/xyz.vcf"
        vcard = "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:xyz\r\nFN:Test\r\nEND:VCARD\r\n"
        xml_body = _make_report_response([
            (full_href, '"etag-2"', vcard),
        ])

        session = MagicMock()
        resp = MagicMock()
        resp.content = xml_body
        resp.raise_for_status = MagicMock()
        session.request.return_value = resp

        contacts = list_contacts(session, abook_url)
        assert contacts[0][0] == full_href
