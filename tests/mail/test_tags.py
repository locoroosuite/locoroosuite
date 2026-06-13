from unittest.mock import patch, MagicMock

import pytest


def test_create_tag(authed_client):
    client, user_id, account_id = authed_client
    with (
        patch("app.modules.mail.controllers.tags.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.services.cache_db.create_tag") as mock_create_tag,
    ):
        resp = client.post("/app/mail/tags", data={"name": "Important", "account_id": str(account_id)})
    assert resp.status_code == 302
    mock_create_tag.assert_called_once()


def test_tag_view_with_messages_passes_correct_encryption_key(authed_client):
    client, user_id, account_id = authed_client
    from app.shared.keys import get_user_key
    expected_key = get_user_key(user_id)
    mock_row = (1, "Tagged Subject", "sender@example.com", "snippet", "2025-01-01", '["\\Seen"]', "body text")
    with (
        patch("app.modules.mail.controllers.tags.open_cache", return_value=MagicMock()),
        patch("app.modules.mail.controllers.tags._get_or_create_settings", return_value=MagicMock()),
        patch("app.modules.mail.controllers.tags._folder_sidebar_context", return_value=([], [], {}, [], 0, None)) as mock_sidebar,
        patch("app.modules.mail.controllers.tags._consume_send_failure_notice", return_value=None),
        patch("app.modules.mail.controllers.tags._current_undo_action", return_value=None),
        patch("app.modules.mail.controllers.tags._spam_action_enabled", return_value=False),
        patch("app.modules.mail.services.cache_db.list_messages_by_tag", return_value=[mock_row]),
        patch("app.modules.mail.services.cache_db.has_completed_sync", return_value=True),
    ):
        resp = client.get(f"/app/mail/tag/{account_id}/1")
    assert resp.status_code == 200
    sidebar_call_key = mock_sidebar.call_args[0][2]
    assert sidebar_call_key == expected_key, (
        f"_folder_sidebar_context received key={sidebar_call_key!r}, expected encryption key={expected_key!r}"
    )
