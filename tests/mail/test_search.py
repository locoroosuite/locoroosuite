from unittest.mock import patch, MagicMock

import pytest


def test_search_empty_query(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post("/app/mail/search", data={"q": "", "account_id": str(account_id)})
    assert resp.status_code == 200


def test_search_with_results(authed_client):
    client, user_id, account_id = authed_client
    with (
        patch("app.modules.mail.controllers.search.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.controllers.search.search_local", return_value=[]),
        patch("app.modules.mail.controllers.search._get_or_create_settings", return_value=MagicMock()),
        patch("app.modules.mail.controllers.search._format_short_date", return_value=""),
        patch("app.modules.mail.controllers.search.normalize_header_text", return_value=""),
        patch("app.modules.mail.controllers.search.decode_address_header", return_value=""),
        patch("app.modules.mail.controllers.search.normalize_preview_text", return_value=""),
        patch("app.modules.mail.controllers.search._imap_for_account", return_value=(MagicMock(), MagicMock())),
        patch("app.modules.mail.controllers.search.push_event"),
        patch("app.modules.mail.controllers.search.list_folders", return_value=[]),
        patch("app.modules.mail.controllers.search.safe_logout"),
    ):
        resp = client.post("/app/mail/search", data={"q": "test", "account_id": str(account_id)})
    assert resp.status_code == 200


def test_search_renders_clickable_rows(authed_client):
    client, user_id, account_id = authed_client
    fake_row = [42, 1001, "INBOX", "Hello", "alice@example.com", "bob@example.com", "2025-01-01", "\\Seen", "<p>body</p>", 0, "<msg123@example.com>", "thread-1", "body text"]
    with (
        patch("app.modules.mail.controllers.search.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.controllers.search.search_local", return_value=[fake_row]),
        patch("app.modules.mail.controllers.search._get_or_create_settings", return_value=MagicMock(timezone="UTC")),
        patch("app.modules.mail.controllers.search._format_short_date", return_value="Jan 1"),
        patch("app.modules.mail.controllers.search.normalize_header_text", side_effect=lambda x: x),
        patch("app.modules.mail.controllers.search.decode_address_header", side_effect=lambda x: x),
        patch("app.modules.mail.controllers.search.normalize_preview_text", side_effect=lambda x, **kw: x),
        patch("app.modules.mail.controllers.search._imap_for_account", return_value=(MagicMock(), MagicMock())),
        patch("app.modules.mail.controllers.search.push_event"),
        patch("app.modules.mail.controllers.search.list_folders", return_value=[]),
        patch("app.modules.mail.controllers.search.safe_logout"),
    ):
        resp = client.post("/app/mail/search", data={"q": "hello", "account_id": str(account_id)})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "message-row" in html
    assert "data-message-url" in html
    assert "data-star-toggle" in html
    assert "data-action=\"flag\"" in html
    assert "data-action=\"archive\"" in html
    assert "data-action=\"delete\"" in html


def test_search_empty_shows_no_results_message(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post("/app/mail/search", data={"q": "", "account_id": str(account_id)})
    html = resp.data.decode()
    assert "No messages found" in html
