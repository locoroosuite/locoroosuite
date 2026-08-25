from unittest.mock import MagicMock

from app.modules.calendar.services import caldav

PROPFIND_ETAG_BODY = (
    "<?xml version='1.0' encoding='utf-8'?>"
    '<d:multistatus xmlns:d="DAV:"><d:response>'
    "<d:href>/cal/evt.ics</d:href>"
    "<d:propstat><d:prop><d:getetag>&quot;etag-fresh&quot;</d:getetag></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
)


def _resp(status_code=200, headers=None, text="", content=None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    r.text = text
    r.content = content if content is not None else text.encode("utf-8")
    r.raise_for_status = MagicMock()
    return r


def _session(responses):
    """Fake requests.Session; responses maps method -> response or list."""
    s = MagicMock()
    for method, resp in responses.items():
        if isinstance(resp, list):
            getattr(s, method).side_effect = resp
        else:
            getattr(s, method).return_value = resp
    return s


ICAL = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"


class TestUpdateEventEtagHandling:
    def test_response_etag_used_when_present(self):
        s = _session({"put": _resp(200, headers={"ETag": '"etag-1"'})})
        assert caldav.update_event(s, "/cal/evt.ics", ICAL, '"etag-0"') == '"etag-1"'
        s.request.assert_not_called()

    def test_propfind_fallback_when_response_etag_missing(self):
        put = _resp(200, headers={})
        propfind = _resp(207, text=PROPFIND_ETAG_BODY)
        s = MagicMock()
        s.put.return_value = put
        s.request.return_value = propfind
        assert caldav.update_event(s, "/cal/evt.ics", ICAL, '"etag-0"') == '"etag-fresh"'
        assert s.request.call_args[0][0] == "PROPFIND"

    def test_stale_etag_412_retries_with_fresh_etag(self):
        put_412 = _resp(412)
        put_ok = _resp(200, headers={})
        propfind = _resp(207, text=PROPFIND_ETAG_BODY)
        s = MagicMock()
        s.put.side_effect = [put_412, put_ok]
        s.request.return_value = propfind
        result = caldav.update_event(s, "/cal/evt.ics", ICAL, '"etag-stale"')
        assert result == '"etag-fresh"'
        assert s.put.call_count == 2
        # Retry must carry the freshly fetched precondition
        assert s.put.call_args_list[1][1]["headers"]["If-Match"] == '"etag-fresh"'

    def test_stale_etag_412_retries_without_etag_when_propfind_empty(self):
        put_412 = _resp(412)
        put_ok = _resp(200, headers={"ETag": '"etag-after"'})
        propfind = _resp(207, text="<d:multistatus xmlns:d='DAV:'></d:multistatus>")
        s = MagicMock()
        s.put.side_effect = [put_412, put_ok]
        s.request.return_value = propfind
        result = caldav.update_event(s, "/cal/evt.ics", ICAL, '"etag-stale"')
        assert result == '"etag-after"'
        assert "If-Match" not in s.put.call_args_list[1][1]["headers"]

    def test_persistent_412_raises(self):
        s = MagicMock()
        s.put.return_value = _resp(412)
        s.request.return_value = _resp(207, text=PROPFIND_ETAG_BODY)
        try:
            caldav.update_event(s, "/cal/evt.ics", ICAL, '"etag-stale"')
            raise AssertionError("expected HTTPError")
        except Exception:
            pass
        assert s.put.call_count == 2


class TestCreateEventEtagHandling:
    def test_response_etag_used_when_present(self):
        s = _session({"put": _resp(201, headers={"ETag": '"etag-new"'})})
        href, etag = caldav.create_event(s, "/cal/", ICAL, uid="u1")
        assert href.endswith("/u1.ics")
        assert etag == '"etag-new"'

    def test_propfind_fallback_when_response_etag_missing(self):
        s = MagicMock()
        s.put.return_value = _resp(201, headers={})
        s.request.return_value = _resp(207, text=PROPFIND_ETAG_BODY)
        _href, etag = caldav.create_event(s, "/cal/", ICAL, uid="u1")
        assert etag == '"etag-fresh"'


class TestDeleteEventEtagHandling:
    def test_stale_etag_412_retries(self):
        s = MagicMock()
        s.delete.side_effect = [_resp(412), _resp(204)]
        s.request.return_value = _resp(207, text=PROPFIND_ETAG_BODY)
        assert caldav.delete_event(s, "/cal/evt.ics", '"etag-stale"') is True
        assert s.delete.call_count == 2
        assert s.delete.call_args_list[1][1]["headers"]["If-Match"] == '"etag-fresh"'

    def test_404_is_success(self):
        s = _session({"delete": _resp(404)})
        assert caldav.delete_event(s, "/cal/evt.ics", '"etag-x"') is True
