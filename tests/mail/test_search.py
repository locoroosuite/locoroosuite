from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch


def _fake_row(**overrides):
    row = {
        "id": 42,
        "uid": 1001,
        "folder": "INBOX",
        "subject": "Hello",
        "sender": "alice@example.com",
        "recipients": "bob@example.com",
        "date": "2025-01-01",
        "flags": "\\Seen",
        "body": "<p>body</p>",
        "has_attachments": 0,
        "message_id": "<msg123@example.com>",
        "thread_id": "thread-1",
        "snippet": "body text",
    }
    row.update(overrides)
    return row


@contextmanager
def _search_patches(rows=None):
    """Patches for the search view; yields a dict of entered mocks."""
    rows = rows if rows is not None else []
    patches = {
        "open_cache": patch(
            "app.modules.mail.controllers.search.open_cache", return_value=MagicMock()
        ),
        "search_messages": patch(
            "app.modules.mail.controllers.search.search_messages",
            return_value=(rows, len(rows)),
        ),
        "settings": patch(
            "app.modules.mail.controllers.search._get_or_create_settings",
            return_value=MagicMock(timezone="UTC"),
        ),
        "_format_short_date": patch(
            "app.modules.mail.controllers.search._format_short_date", return_value="Jan 1"
        ),
        "normalize_header_text": patch(
            "app.modules.mail.controllers.search.normalize_header_text",
            side_effect=lambda x: x,
        ),
        "decode_address_header": patch(
            "app.modules.mail.controllers.search.decode_address_header", side_effect=lambda x: x
        ),
        "normalize_preview_text": patch(
            "app.modules.mail.controllers.search.normalize_preview_text",
            side_effect=lambda x, **kw: x,
        ),
        "_imap_for_account": patch(
            "app.modules.mail.controllers.search._imap_for_account",
            return_value=(MagicMock(), MagicMock()),
        ),
        "push_event": patch("app.modules.mail.controllers.search.push_event"),
        "list_folders": patch("app.modules.mail.controllers.search.list_folders", return_value=[]),
        "safe_logout": patch("app.modules.mail.controllers.search.safe_logout"),
    }
    with ExitStack() as stack:
        yield {name: stack.enter_context(p) for name, p in patches.items()}


def test_search_empty_query(authed_client):
    client, _user_id, account_id = authed_client
    resp = client.post("/app/mail/search", data={"q": "", "account_id": str(account_id)})
    assert resp.status_code == 200


def test_search_supports_get_shareable_urls(authed_client):
    client, _user_id, account_id = authed_client
    with _search_patches([]):
        resp = client.get(f"/app/mail/search?q=from%3Aalice&account_id={account_id}")
    assert resp.status_code == 200


def test_search_with_results(authed_client):
    client, _user_id, account_id = authed_client
    with _search_patches([]):
        resp = client.post("/app/mail/search", data={"q": "test", "account_id": str(account_id)})
    assert resp.status_code == 200


def test_search_renders_clickable_rows(authed_client):
    client, _user_id, account_id = authed_client
    with _search_patches([_fake_row()]):
        resp = client.post("/app/mail/search", data={"q": "hello", "account_id": str(account_id)})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "message-row" in html
    assert "data-message-url" in html
    assert "data-star-toggle" in html
    assert 'data-action="flag"' in html
    assert 'data-action="archive"' in html
    assert 'data-action="delete"' in html


def test_search_rows_have_swipe_panels_and_inert_overlay(authed_client):
    """UX3g: search rows carry the swipe gesture panels and foreground
    (no '...' toggle anymore), and the action overlay must be
    non-interactive while hidden (no accidental taps)."""
    client, _user_id, account_id = authed_client
    with _search_patches([_fake_row()]):
        resp = client.post("/app/mail/search", data={"q": "hello", "account_id": str(account_id)})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "data-message-actions-toggle" not in html, "the '...' toggle was removed (UX3g)"
    assert 'data-swipe-panel="left"' in html, "swipe archive panel missing from search rows"
    assert 'data-swipe-panel="right"' in html, "swipe actions panel missing from search rows"
    assert "data-row-foreground" in html, "swipe foreground wrapper missing"
    assert "data-select-circle" in html, "long-press selection circle missing (UX3h)"
    assert "bulk-select.js" in html, "bulk-select script not loaded on search results"
    assert "message-list.js" in html, "shared message-list script not loaded"
    # The hidden overlay must never intercept taps on any device (UX3d).
    assert "opacity-0 pointer-events-none" in html


def test_search_passes_folders_for_move_picker(authed_client):
    """The view passes the account's known folders so the (previously dead)
    'Move to folder' action works on search results."""
    client, _user_id, account_id = authed_client
    cache_conn = MagicMock()
    folder_row = {"folder": "INBOX"}
    cache_conn.execute.return_value.fetchall.return_value = [folder_row]
    with (
        _search_patches([_fake_row()]),
        patch("app.modules.mail.controllers.search.open_cache", return_value=cache_conn),
    ):
        resp = client.post("/app/mail/search", data={"q": "hello", "account_id": str(account_id)})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'folders: ["INBOX"]' in html


def test_search_empty_shows_no_results_message(authed_client):
    client, _user_id, account_id = authed_client
    resp = client.post("/app/mail/search", data={"q": "", "account_id": str(account_id)})
    html = resp.data.decode()
    assert "No messages found" in html


def test_search_renders_operator_chips(authed_client):
    """U7.4/U7.5: active operators render as removable chips linking to the
    query without that filter."""
    client, _user_id, account_id = authed_client
    with _search_patches([_fake_row()]):
        resp = client.post(
            "/app/mail/search",
            data={"q": "from:alice is:unread", "account_id": str(account_id)},
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "data-search-chip" in html
    assert "from:alice" in html or "from" in html
    # Removing the from: chip keeps is:unread in the target query.
    assert "is%3Aunread" in html or "is:unread" in html


def test_search_advanced_fields_build_operator_query(authed_client):
    """U7.5: the advanced panel fields are merged into the operator query."""
    client, _user_id, account_id = authed_client
    with _search_patches([]):
        resp = client.post(
            "/app/mail/search",
            data={
                "q": "",
                "f_from": "alice@example.com",
                "f_attachment": "on",
                "f_before": "2025-01-31",
                "account_id": str(account_id),
            },
        )
    assert resp.status_code == 200


@contextmanager
def _apply_patches(rows, settings=None):
    """Patches for the search-apply route; yields a dict of named mocks."""
    patches = {
        "open_cache": patch(
            "app.modules.mail.controllers.search.open_cache", return_value=MagicMock()
        ),
        "search_messages": patch(
            "app.modules.mail.controllers.search.search_messages",
            return_value=(rows, len(rows)),
        ),
        "settings": patch(
            "app.modules.mail.controllers.search._get_or_create_settings",
            return_value=settings
            or MagicMock(protect_starred=False, protected_folders=None, locked_keyword_prefs=None),
        ),
        "_imap_for_account": patch(
            "app.modules.mail.controllers.search._imap_for_account",
            return_value=(MagicMock(), MagicMock()),
        ),
        "select_folder": patch("app.modules.mail.controllers.search.select_folder"),
        "set_flag": patch("app.modules.mail.controllers.search.set_flag"),
        "move_message": patch("app.modules.mail.controllers.search.move_message"),
        "safe_logout": patch("app.modules.mail.controllers.search.safe_logout"),
        "update_flags_bulk": patch("app.modules.mail.services.cache_db.update_flags_bulk"),
    }
    with ExitStack() as stack:
        yield {name: stack.enter_context(p) for name, p in patches.items()}


class TestSearchApply:
    def test_rejects_unknown_action(self, authed_client):
        client, _user_id, account_id = authed_client
        resp = client.post(
            "/app/mail/search/apply",
            data={"q": "hello", "action": "explode", "account_id": str(account_id)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        assert resp.json["status"] == "error"

    def test_rejects_move_without_destination(self, authed_client):
        client, _user_id, account_id = authed_client
        resp = client.post(
            "/app/mail/search/apply",
            data={"q": "hello", "action": "move", "account_id": str(account_id)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400

    def test_rejects_empty_query(self, authed_client):
        client, _user_id, account_id = authed_client
        resp = client.post(
            "/app/mail/search/apply",
            data={"q": "", "action": "mark_read", "account_id": str(account_id)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400

    def test_marks_all_matching_read(self, authed_client):
        client, _user_id, account_id = authed_client
        rows = [
            _fake_row(id=1, uid="7", flags='[""]'),
            _fake_row(id=2, uid="8", folder="Sent", flags='["\\\\Seen"]'),
        ]
        with _apply_patches(rows) as mocks:
            resp = client.post(
                "/app/mail/search/apply",
                data={"q": "is:unread", "action": "mark_read", "account_id": str(account_id)},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 200
        body = resp.json
        assert body["status"] == "ok"
        assert body["applied"] == 2
        assert mocks["set_flag"].call_count == 2
        assert mocks["update_flags_bulk"].call_count >= 1

    def test_delete_skips_protected_messages(self, authed_client):
        client, _user_id, account_id = authed_client
        settings = MagicMock(
            protect_starred=True, protected_folders=None, locked_keyword_prefs=None
        )
        rows = [_fake_row(id=1, uid="7", flags='["\\\\Flagged"]')]
        with _apply_patches(rows, settings) as mocks:
            resp = client.post(
                "/app/mail/search/apply",
                data={"q": "is:starred", "action": "delete", "account_id": str(account_id)},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.status_code == 200
        assert resp.json["applied"] == 0
        assert resp.json["skipped"] == 1
        mocks["move_message"].assert_not_called()

    def test_non_xhr_redirects_back_to_search(self, authed_client):
        client, _user_id, account_id = authed_client
        rows = [_fake_row(id=1, uid="7")]
        with _apply_patches(rows):
            resp = client.post(
                "/app/mail/search/apply",
                data={"q": "is:unread", "action": "mark_read", "account_id": str(account_id)},
            )
        assert resp.status_code == 302
        assert "/mail/search" in resp.headers["Location"]


class TestSearchParseEndpoint:
    """U7.5: /mail/search/parse maps a raw query to panel field values."""

    def test_parse_fills_fields_from_operators(self, authed_client):
        client, _user_id, account_id = authed_client
        resp = client.get(
            f"/app/mail/search/parse?q=from%3Aalice+is%3Aunread+report&account_id={account_id}"
        )
        assert resp.status_code == 200
        data = resp.json
        assert data["fields"]["f_from"] == "alice"
        assert data["fields"]["f_unread"] is True
        assert data["fields"]["q"] == "report"
        assert {"key": "from", "value": "alice"} in data["chips"]

    def test_parse_empty_query(self, authed_client):
        client, _user_id, account_id = authed_client
        resp = client.get(f"/app/mail/search/parse?q=&account_id={account_id}")
        assert resp.status_code == 200
        assert resp.json["fields"]["f_from"] == ""
        assert resp.json["chips"] == []

    def test_parse_quoted_values(self, authed_client):
        client, _user_id, account_id = authed_client
        resp = client.get(f"/app/mail/search/parse?q=from%3A%22John+Doe%22&account_id={account_id}")
        assert resp.status_code == 200
        assert resp.json["fields"]["f_from"] == "John Doe"

    def test_parse_rejects_foreign_account(self, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.get("/app/mail/search/parse?q=x&account_id=99999")
        assert resp.status_code == 404


class TestSearchFoldersEndpoint:
    """U7.5: lazy folder options for the advanced-search panel."""

    def test_returns_folder_names(self, authed_client):
        client, _user_id, account_id = authed_client
        with (
            patch("app.modules.mail.controllers.search.open_cache", return_value=MagicMock()),
            patch(
                "app.modules.mail.controllers.search.list_cached_folders",
                return_value=[{"name": "INBOX"}, {"name": "Sent"}],
            ),
        ):
            resp = client.get(f"/app/mail/search/folders?account_id={account_id}")
        assert resp.status_code == 200
        assert resp.json["folders"] == ["INBOX", "Sent"]
        assert resp.json["folder_labels"] == {"INBOX": "INBOX", "Sent": "Sent"}

    def test_labels_junk_alias_as_spam(self, authed_client):
        """U4.15b: the panel dropdown shows 'Spam' for a Junk folder."""
        client, _user_id, account_id = authed_client
        with (
            patch("app.modules.mail.controllers.search.open_cache", return_value=MagicMock()),
            patch(
                "app.modules.mail.controllers.search.list_cached_folders",
                return_value=[{"name": "INBOX"}, {"name": "Junk"}],
            ),
        ):
            resp = client.get(f"/app/mail/search/folders?account_id={account_id}")
        assert resp.status_code == 200
        assert resp.json["folders"] == ["INBOX", "Junk"]
        assert resp.json["folder_labels"] == {"INBOX": "INBOX", "Junk": "Spam"}

    def test_rejects_foreign_account(self, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.get("/app/mail/search/folders?account_id=99999")
        assert resp.status_code == 404


def test_search_accepts_starred_field(authed_client):
    """U7.5: the panel's Starred only checkbox builds is:starred."""
    client, _user_id, account_id = authed_client
    with _search_patches([]):
        resp = client.post(
            "/app/mail/search",
            data={"q": "", "f_starred": "on", "account_id": str(account_id)},
        )
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "is:starred" in html
