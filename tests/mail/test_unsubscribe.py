"""U8.10: header-based unsubscribe (List-Unsubscribe, RFC 2369).

The affordance is sourced from the transport header because body footers can
be sender-corrupted (see the Klaviyo double-rendered anchor covered by
``TestMalformedHrefSanitizationU8_2a`` in test_email_rendering.py).
"""

import re
from email import message_from_string
from unittest.mock import MagicMock, patch

from app.modules.mail.utils.unsubscribe import extract_list_unsubscribe

# Mock data contract: mirrors the message dict _load_message_detail returns —
# cache-row columns (id, uid, folder, subject, sender, recipients, date,
# flags, snippet, body, body_html, has_attachments, message_id, thread_id, cc)
# plus the U8.10 ``unsubscribe`` key ({kind,url} or {kind,to,subject} or None).
MOCK_MSG_BASE = {
    "id": 1,
    "uid": "100",
    "folder": "INBOX",
    "subject": "Did we get it right?",
    "sender": "Francesca <francesca@francescas.com.au>",
    "recipients": "recip@test.com",
    "date": "date",
    "flags": '["\\\\Seen"]',
    "snippet": "body text",
    "body": "",
    "body_html": "<p>html body</p>",
    "has_attachments": 0,
    "message_id": "<msg-id@test.com>",
    "thread_id": "thread-123",
    "cc": "",
}

THREAD_DATA = [
    {
        "id": 2,
        "uid": "99",
        "subject": "Re: Did we get it right?",
        "sender": "Alice <alice@test.com>",
        "sender_display": "Alice",
        "sender_email": "alice@test.com",
        "recipients": "recip@test.com",
        "recipients_display": "recip",
        "date_display": "09:00",
        "date_ts": 1704096000,
        "is_current": False,
        "is_sent": False,
        "is_draft": False,
        "is_bounce": False,
        "snippet": "Earlier message",
        "body_html": "<html>earlier</html>",
        "has_attachments": False,
        "cc": "",
    },
    {
        "id": 1,
        "uid": "100",
        "subject": "Did we get it right?",
        "sender": "Francesca <francesca@francescas.com.au>",
        "sender_display": "Francesca",
        "sender_email": "francesca@francescas.com.au",
        "recipients": "recip@test.com",
        "recipients_display": "recip",
        "date_display": "10:00",
        "date_ts": 1704099600,
        "is_current": True,
        "is_sent": False,
        "is_draft": False,
        "is_bounce": False,
        "snippet": "body text",
        "body_html": "<html>current body</html>",
        "has_attachments": False,
        "cc": "",
    },
]

# The production Francesca's/Klaviyo header (folded, https + mailto entries).
RAW_HTTPS_AND_MAILTO = (
    "Return-Path: <bounce-kl_29775393165@example.klaviyomail.com>\n"
    "List-Unsubscribe: <https://manage.kmail-lists.com/subscriptions/unsubscribe"
    "?a=Y5Eitp&c=01M16F7NKEG36M52993RWHQVCY&k=3345b9a34dee3d19f24b66472f6d8c6a>,\n"
    " <mailto:unsub1-01M16F7NKEG36M52993RWHQVCY-LE2UK2LUOA@shared.klaviyomail.com"
    "?subject=request%20unsubscribe%20ME6VSNKFNF2HAJT>\n"
    "List-Unsubscribe-Post: List-Unsubscribe=One-Click\n"
    "\n"
    "Ciao friend.\n"
)


def _detail_return(unsubscribe=None):
    msg = dict(MOCK_MSG_BASE)
    msg["unsubscribe"] = unsubscribe
    return (msg, "<p>body</p>", [], ["\\Seen"], ("", ""), [], "")


class TestExtractListUnsubscribe:
    def test_https_preferred_over_mailto_with_folded_header(self):
        info = extract_list_unsubscribe(message_from_string(RAW_HTTPS_AND_MAILTO))
        assert info == {
            "kind": "url",
            "url": (
                "https://manage.kmail-lists.com/subscriptions/unsubscribe"
                "?a=Y5Eitp&c=01M16F7NKEG36M52993RWHQVCY&k=3345b9a34dee3d19f24b66472f6d8c6a"
            ),
        }

    def test_mailto_only_decodes_subject(self):
        raw = (
            "List-Unsubscribe: <mailto:unsub-01M@shared.klaviyomail.com"
            "?subject=request%20unsubscribe%20ME6VSNKFNF2HAJT>\n"
            "\nbody\n"
        )
        info = extract_list_unsubscribe(message_from_string(raw))
        assert info == {
            "kind": "mailto",
            "to": "unsub-01M@shared.klaviyomail.com",
            "subject": "request unsubscribe ME6VSNKFNF2HAJT",
        }

    def test_mailto_without_subject_yields_empty_subject(self):
        raw = "List-Unsubscribe: <mailto:leave-list@example.com>\n\nbody\n"
        info = extract_list_unsubscribe(message_from_string(raw))
        assert info == {"kind": "mailto", "to": "leave-list@example.com", "subject": ""}

    def test_url_folded_mid_token_is_restored(self):
        raw = "List-Unsubscribe: <https://example.com/u?se=ruben%40loc\n oroo.net&m=1>\n\nbody\n"
        info = extract_list_unsubscribe(message_from_string(raw))
        assert info == {"kind": "url", "url": "https://example.com/u?se=ruben%40locoroo.net&m=1"}

    def test_missing_header_returns_none(self):
        raw = "Subject: hi\n\nbody\n"
        assert extract_list_unsubscribe(message_from_string(raw)) is None

    def test_no_angle_brackets_returns_none(self):
        raw = "List-Unsubscribe: https://example.com/unsub\n\nbody\n"
        assert extract_list_unsubscribe(message_from_string(raw)) is None

    def test_non_http_scheme_returns_none(self):
        raw = "List-Unsubscribe: <ftp://example.com/unsub>\n\nbody\n"
        assert extract_list_unsubscribe(message_from_string(raw)) is None

    def test_addressless_mailto_returns_none(self):
        raw = "List-Unsubscribe: <mailto:?subject=x>\n\nbody\n"
        assert extract_list_unsubscribe(message_from_string(raw)) is None

    def test_none_raw_message_returns_none(self):
        assert extract_list_unsubscribe(None) is None

    def test_quoted_url_segment_skipped_falls_back_to_mailto(self):
        raw = 'List-Unsubscribe: <https://example.com/a"b>, <mailto:unsub@example.com>\n\nbody\n'
        info = extract_list_unsubscribe(message_from_string(raw))
        assert info == {"kind": "mailto", "to": "unsub@example.com", "subject": ""}


class TestMessageViewUnsubscribeChip:
    def _get(self, client, account_id, detail_return, thread_data=THREAD_DATA):
        url = f"/app/mail/message/{account_id}/1"
        with (
            patch("app.modules.mail.controllers.message._load_message_detail") as mock_load,
            patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings,
            patch("app.modules.mail.controllers.message.open_cache") as mock_cache,
            patch("app.modules.mail.controllers.message.move_destination_folders") as mock_folders,
            patch("app.modules.mail.controllers.message._spam_action_enabled") as mock_spam,
            patch("app.modules.mail.controllers.message._load_thread_for_detail") as mock_thread,
        ):
            mock_load.return_value = detail_return
            mock_settings.return_value = MagicMock()
            mock_cache.return_value = MagicMock()
            mock_folders.return_value = []
            mock_spam.return_value = False
            mock_thread.return_value = thread_data
            resp = client.get(url)
        assert resp.status_code == 200
        return resp.data.decode()

    def test_https_entry_renders_chip_next_to_sender(self, app, authed_client):
        client, _user_id, account_id = authed_client
        unsub = {
            "kind": "url",
            "url": "https://manage.kmail-lists.com/subscriptions/unsubscribe?a=Y5Eitp",
        }
        html = self._get(client, account_id, _detail_return(unsub))
        assert html.count("data-unsubscribe") == 1  # current message only
        anchor = re.search(r"<a[^>]*data-unsubscribe[^>]*>", html)
        assert anchor is not None
        tag = anchor.group(0)
        assert 'href="https://manage.kmail-lists.com/subscriptions/unsubscribe?a=Y5Eitp"' in tag
        assert 'target="_blank"' in tag
        assert 'rel="noopener noreferrer"' in tag
        assert "data-no-row-nav" in tag
        assert ">Unsubscribe</a>" in html

    def test_mailto_entry_links_to_prefilled_compose(self, app, authed_client):
        client, _user_id, account_id = authed_client
        unsub = {
            "kind": "mailto",
            "to": "unsub-01M@shared.klaviyomail.com",
            "subject": "request unsubscribe ME6VSNKFNF2HAJT",
        }
        html = self._get(client, account_id, _detail_return(unsub))
        anchor = re.search(r"<a[^>]*data-unsubscribe[^>]*>", html)
        assert anchor is not None
        tag = anchor.group(0)
        assert "/app/mail/compose" in tag
        assert "to=unsub-01M%40shared.klaviyomail.com" in tag or (
            "to=unsub-01M@shared.klaviyomail.com" in tag
        )
        assert "subject=request" in tag

    def test_no_unsubscribe_field_renders_no_chip(self, app, authed_client):
        client, _user_id, account_id = authed_client
        html = self._get(client, account_id, _detail_return(None))
        assert "data-unsubscribe" not in html

    def test_requires_auth(self, client):
        resp = client.get("/app/mail/message/1/1")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


class TestPreviewPaneUnsubscribe:
    def _get(self, client, account_id, detail_return):
        url = f"/app/mail/message/{account_id}/1/preview"
        with (
            patch("app.modules.mail.controllers.message._load_message_detail") as mock_load,
            patch("app.modules.mail.controllers.message._get_or_create_settings") as mock_settings,
        ):
            mock_load.return_value = detail_return
            mock_settings.return_value = MagicMock()
            resp = client.get(url)
        assert resp.status_code == 200
        return resp.data.decode()

    def test_https_entry_renders_row(self, app, authed_client):
        client, _user_id, account_id = authed_client
        unsub = {"kind": "url", "url": "https://example.com/unsub?a=1"}
        html = self._get(client, account_id, _detail_return(unsub))
        assert "data-unsubscribe-row" in html
        assert 'href="https://example.com/unsub?a=1"' in html
        assert 'rel="noopener noreferrer"' in html

    def test_mailto_entry_renders_compose_link(self, app, authed_client):
        client, _user_id, account_id = authed_client
        unsub = {"kind": "mailto", "to": "unsub@example.com", "subject": "bye"}
        html = self._get(client, account_id, _detail_return(unsub))
        assert "data-unsubscribe-row" in html
        assert "/app/mail/compose" in html

    def test_no_field_renders_no_row(self, app, authed_client):
        client, _user_id, account_id = authed_client
        html = self._get(client, account_id, _detail_return(None))
        assert "data-unsubscribe-row" not in html


class TestComposeUnsubscribePrefill:
    def test_to_and_subject_prefill_compose(self, app, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.get(
            "/app/mail/compose",
            query_string={
                "to": "unsub-01M@shared.klaviyomail.com",
                "subject": "request unsubscribe ME6VSNKFNF2HAJT",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'value="unsub-01M@shared.klaviyomail.com"' in html
        assert 'value="request unsubscribe ME6VSNKFNF2HAJT"' in html

    def test_no_to_param_leaves_compose_empty(self, app, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.get("/app/mail/compose", query_string={"subject": "stale"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="to-hidden" value=""' in html
        assert 'value="stale"' not in html

    def test_to_param_with_markup_is_neutralized(self, app, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.get(
            "/app/mail/compose",
            query_string={"to": "<script>alert(1)</script>x@example.com"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "<script>alert(1)</script>" not in html
        assert "x@example.com" in html
