import json
from unittest.mock import patch, MagicMock



def test_mailbox_redirects_to_inbox(authed_client):
    client, user_id, account_id = authed_client
    resp = client.get("/app/mail/")
    assert resp.status_code == 302
    assert "INBOX" in resp.headers["Location"]


_empty_pagination = {
    "total_threads": 0,
    "total_messages": 0,
    "current_page": 1,
    "total_pages": 1,
    "per_page": 50,
}


def test_folder_view(authed_client, app):
    client, user_id, account_id = authed_client
    mock_settings = MagicMock()
    mock_settings.timezone = "UTC"
    app.sync_manager.set_active_account.return_value = None
    app.sync_manager.set_active_folder.return_value = None
    app.sync_manager.enqueue_sync.return_value = False
    with (
        patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.controllers.mailbox._build_threads", return_value=({}, _empty_pagination)),
        patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=mock_settings),
        patch("app.modules.mail.controllers.mailbox._folder_sidebar_context", return_value=([], [], {}, [], 0, None)),
        patch("app.modules.mail.controllers.mailbox._snippet_debug_enabled", return_value=False),
        patch("app.modules.mail.controllers.mailbox._consume_send_failure_notice", return_value=None),
        patch("app.modules.mail.controllers.mailbox._current_undo_action", return_value=None),
        patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
        patch("app.modules.mail.services.cache_db.has_completed_sync", return_value=True),
    ):
        resp = client.get(f"/app/mail/folder/{account_id}/INBOX")
    assert resp.status_code == 200


def test_folder_messages_json(authed_client):
    client, user_id, account_id = authed_client
    with (
        patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.controllers.mailbox._build_threads", return_value=({}, _empty_pagination)),
        patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=MagicMock()),
        patch("app.modules.mail.controllers.mailbox._snippet_debug_enabled", return_value=False),
        patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
    ):
        resp = client.get(f"/app/mail/folder/{account_id}/INBOX/messages")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "html" in data
    assert "thread_count" in data
    assert "total_threads" in data
    assert "total_messages" in data
    assert "current_page" in data
    assert "total_pages" in data


def test_mark_all_read(authed_client):
    client, user_id, account_id = authed_client
    mock_client = MagicMock()
    with (
        patch("app.modules.mail.controllers.mailbox.decrypt_with_key", return_value="secret"),
        patch("app.modules.mail.controllers.mailbox._imap_for_account", return_value=(mock_client, MagicMock())),
        patch("app.modules.mail.controllers.mailbox.select_folder"),
    ):
        resp = client.post(f"/app/mail/folder/{account_id}/INBOX/mark-all-read")
    assert resp.status_code == 302
    mock_client.store.assert_called_once()
    mock_client.logout.assert_called_once()


def test_create_folder(authed_client):
    client, user_id, account_id = authed_client
    mock_client = MagicMock()
    with (
        patch("app.modules.mail.controllers.mailbox.decrypt_with_key", return_value="secret"),
        patch("app.modules.mail.controllers.mailbox._imap_for_account", return_value=(mock_client, MagicMock())),
        patch("app.modules.mail.controllers.mailbox.create_folder"),
    ):
        resp = client.post(f"/app/mail/folder/{account_id}/create", data={"name": "Archive"})
    assert resp.status_code == 302
    mock_client.logout.assert_called_once()


def test_toggle_pin_folder(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post(f"/app/mail/folder/{account_id}/INBOX/pin")
    assert resp.status_code == 302


def test_delete_system_folder_refused_as_json(authed_client):
    # System folders are always protected; the XHR path must return a structured
    # 409 instead of a full-page redirect so the sidebar indicator is not stranded.
    client, user_id, account_id = authed_client
    resp = client.post(
        f"/app/mail/folder/{account_id}/INBOX/delete",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 409
    data = json.loads(resp.data)
    assert data["status"] == "error"
    assert "protected" in data["error"].lower()


def test_delete_system_folder_redirects_when_not_xhr(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post(f"/app/mail/folder/{account_id}/INBOX/delete")
    assert resp.status_code == 302


def test_delete_user_protected_folder_refused_as_json(authed_client):
    client, user_id, account_id = authed_client
    mock_settings = MagicMock()
    mock_settings.protected_folders = json.dumps(["Projects"])
    with patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=mock_settings):
        resp = client.post(
            f"/app/mail/folder/{account_id}/Projects/delete",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 409
    data = json.loads(resp.data)
    assert data["status"] == "error"
    assert "protected" in data["error"].lower()


def test_delete_folder_success_returns_redirect_json(authed_client):
    client, user_id, account_id = authed_client
    mock_client = MagicMock()
    with (
        patch("app.modules.mail.controllers.mailbox.decrypt_with_key", return_value="secret"),
        patch("app.modules.mail.controllers.mailbox._imap_for_account", return_value=(mock_client, MagicMock())),
        patch("app.modules.mail.controllers.mailbox.imap_delete_folder"),
        patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.services.cache_db.delete_folder_in_cache"),
    ):
        resp = client.post(
            f"/app/mail/folder/{account_id}/Projects/delete",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert "INBOX" in data["redirect"]


def test_toggle_protect_system_folder_refused_as_json(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post(
        f"/app/mail/folder/{account_id}/INBOX/protect",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 409
    data = json.loads(resp.data)
    assert data["status"] == "error"


def test_toggle_protect_user_folder_returns_state(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post(
        f"/app/mail/folder/{account_id}/Projects/protect",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["protected"] is True


def test_remove_account(authed_client):
    client, user_id, account_id = authed_client
    with patch("app.modules.mail.controllers.mailbox.purge_cache"):
        resp = client.post(f"/app/mail/accounts/{account_id}/remove")
    assert resp.status_code == 302


def test_set_active_account(authed_client):
    client, user_id, account_id = authed_client
    resp = client.post("/app/mail/accounts/active", data={"account_id": str(account_id)})
    assert resp.status_code == 302


def test_smart_folder_unread(authed_client):
    client, user_id, account_id = authed_client
    with (
        patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=MagicMock()),
        patch("app.modules.mail.controllers.mailbox._folder_sidebar_context", return_value=([], [], {}, [], 0, None)),
        patch("app.modules.mail.controllers.mailbox._consume_send_failure_notice", return_value=None),
        patch("app.modules.mail.controllers.mailbox._current_undo_action", return_value=None),
        patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
        patch("app.modules.mail.services.cache_db.list_unread", return_value=[]),
        patch("app.modules.mail.services.cache_db.has_completed_sync", return_value=True),
    ):
        resp = client.get(f"/app/mail/smart/{account_id}/unread")
    assert resp.status_code == 200


def test_smart_folder_unread_with_messages_passes_correct_encryption_key(authed_client):
    client, user_id, account_id = authed_client
    from app.shared.keys import get_user_key
    expected_key = get_user_key(user_id)
    mock_row = {
        "id": 1, "subject": "Test Subject", "sender": "sender@example.com",
        "snippet": "snippet", "date": "2025-01-01", "flags": '["\\Seen"]',
        "body": "body text", "folder": "INBOX", "thread_id": "thread-1",
        "recipients": "dest@example.com", "sort_ts": 1735689600,
        "is_bounce": 0, "bounce_reason": None, "original_subject": None,
        "has_attachments": 0,
    }
    with (
        patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.controllers.mailbox._get_or_create_settings", return_value=MagicMock()),
        patch("app.modules.mail.controllers.mailbox._folder_sidebar_context", return_value=([], [], {}, [], 0, None)) as mock_sidebar,
        patch("app.modules.mail.controllers.mailbox._consume_send_failure_notice", return_value=None),
        patch("app.modules.mail.controllers.mailbox._current_undo_action", return_value=None),
        patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
        patch("app.modules.mail.services.cache_db.list_unread", return_value=[mock_row]),
        patch("app.modules.mail.services.cache_db.has_completed_sync", return_value=True),
    ):
        resp = client.get(f"/app/mail/smart/{account_id}/unread")
    assert resp.status_code == 200
    sidebar_call_key = mock_sidebar.call_args[0][2]
    assert sidebar_call_key == expected_key, (
        f"_folder_sidebar_context received key={sidebar_call_key!r}, expected encryption key={expected_key!r}"
    )


def test_unread_excludes_drafts():
    from app.modules.mail.services.folder_sort import UNREAD_EXCLUDED_FOLDERS
    assert "DRAFTS" in UNREAD_EXCLUDED_FOLDERS


def test_folder_view_cache_key_mismatch(authed_client, app):
    from app.shared.cache_errors import CacheKeyMismatchError

    client, user_id, account_id = authed_client
    app.sync_manager.set_active_account.return_value = None
    app.sync_manager.set_active_folder.return_value = None
    app.sync_manager.enqueue_sync.return_value = False
    with patch(
        "app.modules.mail.controllers.mailbox.open_cache",
        side_effect=CacheKeyMismatchError("key mismatch"),
    ):
        resp = client.get(f"/app/mail/folder/{account_id}/INBOX", headers={"Accept": "text/html"})
    assert resp.status_code == 500
    text = resp.data.decode()
    assert "cache key mismatch" in text.lower() or "Cache key mismatch" in text
    assert "Reset cache" in text


def test_folder_view_cache_key_mismatch_json(authed_client, app):
    from app.shared.cache_errors import CacheKeyMismatchError

    client, user_id, account_id = authed_client
    app.sync_manager.set_active_folder.return_value = None
    app.sync_manager.enqueue_sync.return_value = False
    with patch(
        "app.modules.mail.controllers.mailbox.open_cache",
        side_effect=CacheKeyMismatchError("key mismatch"),
    ):
        resp = client.get(
            f"/app/mail/folder/{account_id}/INBOX/messages",
            headers={"Accept": "application/json"},
        )
    assert resp.status_code == 500
    data = json.loads(resp.data)
    assert data["error"]["code"] == "CACHE_KEY_MISMATCH"
    assert data["error"]["account_id"] == account_id


def test_reset_cache_deletes_file_and_redirects(authed_client, app, tmp_path):
    from app.shared.db import db
    from app.shared.models.core import CustomerAccount

    client, user_id, account_id = authed_client
    cache_file = tmp_path / "test_cache.db"
    cache_file.write_text("fake cache data")
    app.sync_manager.enqueue_sync.return_value = False

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        account.cache_db_path = str(cache_file)
        db.session.commit()

    assert cache_file.exists()
    resp = client.post(f"/app/mail/reset-cache/{account_id}")
    assert resp.status_code == 302
    assert "INBOX" in resp.headers["Location"]
    assert not cache_file.exists()

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        assert account.cache_db_path is None


def test_reset_cache_other_user_account_404(authed_client, app, tmp_path):
    from app.shared.db import db
    from app.shared.models.core import User, Domain, CustomerAccount

    client, user_id, account_id = authed_client

    with app.app_context():
        other_user = User(email="other@example.com", role="customer", is_active=True)
        db.session.add(other_user)
        db.session.flush()
        domain = db.session.get(Domain, 1)
        other_account = CustomerAccount(
            customer_id=other_user.id,
            domain_id=domain.id,
            email_address="other@example.com",
            auth_type="password",
            username="other@example.com",
            cache_db_path=str(tmp_path / "other_cache.db"),
        )
        db.session.add(other_account)
        db.session.commit()
        other_account_id = other_account.id

    resp = client.post(f"/app/mail/reset-cache/{other_account_id}")
    assert resp.status_code == 404


def test_reset_cache_no_file_still_redirects(authed_client, app):
    from app.shared.db import db
    from app.shared.models.core import CustomerAccount

    client, user_id, account_id = authed_client
    app.sync_manager.enqueue_sync.return_value = False

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        account.cache_db_path = None
        db.session.commit()

    resp = client.post(f"/app/mail/reset-cache/{account_id}")
    assert resp.status_code == 302
    assert "INBOX" in resp.headers["Location"]


def _badge_row(flagged=True, locked=False):
    return {
        "id": 1, "subject": "Hello", "sender": "s@example.com",
        "sender_display": "s", "sender_tooltip": "s@example.com",
        "snippet": "snippet", "date": "2025-01-01", "date_ts": 0, "sort_ts": 0,
        "date_display": "Jan 1", "flags": (["\\Flagged"] if flagged else []) + (["$Locked"] if locked else []),
        "is_unread": False, "is_flagged": flagged, "folder": "INBOX",
        "thread_id": "t1", "is_sent": False, "is_draft": False,
        "recipients_display": "", "is_bounce": False, "bounce_reason": None,
        "has_attachments": False,
    }


class TestProtectedBadgeRendering:
    """HLD U5.15h: the message list shows a Protected badge so the protection
    state is visible before a delete is attempted."""

    def test_badge_shown_for_starred_when_protect_starred_on(self, app, authed_client):
        client, user_id, account_id = authed_client
        with app.test_request_context():
            from flask import render_template
            html = render_template(
                "message_list.html",
                account=MagicMock(id=account_id, email_address="t@example.com"),
                threads={"Hello": [_badge_row(flagged=True)]},
                spam_action_enabled=False,
                lock_action_enabled=True,
                protect_starred=True,
                snippet_debug=False,
            )
        assert "data-protected-badge" in html
        assert "Protected" in html

    def test_badge_omitted_for_starred_when_protect_starred_off(self, app, authed_client):
        client, user_id, account_id = authed_client
        with app.test_request_context():
            from flask import render_template
            html = render_template(
                "message_list.html",
                account=MagicMock(id=account_id, email_address="t@example.com"),
                threads={"Hello": [_badge_row(flagged=True)]},
                spam_action_enabled=False,
                lock_action_enabled=True,
                protect_starred=False,
                snippet_debug=False,
            )
        assert "data-protected-badge" not in html

    def test_badge_shown_for_locked_regardless_of_policy(self, app, authed_client):
        client, user_id, account_id = authed_client
        with app.test_request_context():
            from flask import render_template
            html = render_template(
                "message_list.html",
                account=MagicMock(id=account_id, email_address="t@example.com"),
                threads={"Hello": [_badge_row(flagged=False, locked=True)]},
                spam_action_enabled=False,
                lock_action_enabled=True,
                protect_starred=False,
                snippet_debug=False,
            )
        assert "data-protected-badge" in html

    def test_badge_omitted_for_plain_message(self, app, authed_client):
        client, user_id, account_id = authed_client
        with app.test_request_context():
            from flask import render_template
            html = render_template(
                "message_list.html",
                account=MagicMock(id=account_id, email_address="t@example.com"),
                threads={"Hello": [_badge_row(flagged=False, locked=False)]},
                spam_action_enabled=False,
                lock_action_enabled=True,
                protect_starred=True,
                snippet_debug=False,
            )
        assert "data-protected-badge" not in html
