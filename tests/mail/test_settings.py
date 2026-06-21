from unittest.mock import patch, MagicMock


def test_settings_page_renders(authed_client):
    client, user_id, account_id = authed_client
    mock_settings = MagicMock()
    mock_settings.polling_interval = 30
    mock_settings.preview_pane_default = True
    mock_settings.sort_order = "newest"
    mock_settings.timezone = "UTC"
    mock_settings.theme = "light"
    mock_settings.spam_action_prefs = "{}"
    with (
        patch("app.modules.mail.controllers.settings._get_or_create_settings", return_value=mock_settings),
        patch("app.modules.mail.controllers.settings._load_spam_action_prefs", return_value={}),
    ):
        resp = client.get("/app/mail/settings")
    assert resp.status_code == 200


def test_settings_post_updates(authed_client):
    client, user_id, account_id = authed_client
    mock_settings = MagicMock()
    mock_settings.polling_interval = 30
    mock_settings.preview_pane_default = True
    mock_settings.sort_order = "newest"
    mock_settings.timezone = "UTC"
    mock_settings.theme = "light"
    mock_settings.spam_action_prefs = "{}"
    with (
        patch("app.modules.mail.controllers.settings._get_or_create_settings", return_value=mock_settings),
        patch("app.modules.mail.controllers.helpers._set_spam_action_enabled"),
    ):
        resp = client.post("/app/mail/settings", data={
            "polling_interval": "60",
            "preview_pane_default": "on",
            "sort_order": "oldest",
            "timezone": "America/New_York",
            "theme": "dark",
        })
    assert resp.status_code == 302
    assert mock_settings.polling_interval == 60
    assert mock_settings.sort_order == "oldest"
    assert mock_settings.timezone == "America/New_York"
    assert mock_settings.theme == "dark"


def test_reset_cache(authed_client):
    client, user_id, account_id = authed_client
    with (
        patch("app.modules.mail.controllers.settings.build_cache_path", return_value="/tmp/test_cache.db"),
        patch("app.modules.mail.controllers.settings.purge_cache") as mock_purge,
    ):
        resp = client.post("/app/mail/settings/reset-cache")
    assert resp.status_code == 302
    mock_purge.assert_called_once()
