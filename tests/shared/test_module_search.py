"""Module-contextual header search (HLD U7.7-U7.11).

The header search box must adapt to the active module: form action,
placeholder, advanced panel, live mode (chat), and the app launcher's
active-module highlight. These tests exercise the shared layout through
one representative page per module.
"""

from unittest.mock import MagicMock, patch

_empty_pagination = {
    "total_threads": 0,
    "total_messages": 0,
    "current_page": 1,
    "total_pages": 1,
    "per_page": 50,
}


def _get(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    return resp.data.decode()


def _mail_page(client, app, account_id):
    """Render a mail page with the mailbox controller's externals mocked."""
    mock_settings = MagicMock()
    mock_settings.timezone = "UTC"
    app.sync_manager.set_active_account.return_value = None
    app.sync_manager.set_active_folder.return_value = None
    app.sync_manager.enqueue_sync.return_value = False
    with (
        patch("app.modules.mail.controllers.mailbox.open_cache", return_value=MagicMock()),
        patch(
            "app.modules.mail.controllers.mailbox._build_threads",
            return_value=({}, _empty_pagination),
        ),
        patch(
            "app.modules.mail.controllers.mailbox._get_or_create_settings",
            return_value=mock_settings,
        ),
        patch(
            "app.modules.mail.controllers.mailbox._folder_sidebar_context",
            return_value=([], [], {}, [], 0, None, {}),
        ),
        patch("app.modules.mail.controllers.mailbox._snippet_debug_enabled", return_value=False),
        patch(
            "app.modules.mail.controllers.mailbox._consume_send_failure_notice", return_value=None
        ),
        patch("app.modules.mail.controllers.mailbox._current_undo_action", return_value=None),
        patch("app.modules.mail.controllers.mailbox._spam_action_enabled", return_value=False),
        patch("app.modules.mail.services.cache_db.has_completed_sync", return_value=True),
    ):
        return _get(client, f"/app/mail/folder/{account_id}/INBOX")


def _contacts_page(client, app, account_id):
    from app.shared.db import db
    from app.shared.models.core import CustomerAccount, Domain

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        domain = db.session.get(Domain, account.domain_id)
        assert domain is not None
        domain.carddav_host = None
        db.session.commit()
    return _get(client, "/app/contacts/")


def _calendar_page(client, app, account_id):
    from app.shared.db import db
    from app.shared.models.core import CustomerAccount, Domain

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        domain = db.session.get(Domain, account.domain_id)
        assert domain is not None
        domain.caldav_host = None
        db.session.commit()
    return _get(client, "/app/calendar/")


def _chat_page(client):
    with patch("app.modules.chat.controllers.views.chat_context", side_effect=Exception("no chat")):
        return _get(client, "/app/chat/")


def _docs_page(client):
    return _get(client, "/app/docs/")


def _render(client, app, account_id, module):
    if module == "mail":
        return _mail_page(client, app, account_id)
    if module == "contacts":
        return _contacts_page(client, app, account_id)
    if module == "calendar":
        return _calendar_page(client, app, account_id)
    if module == "chat":
        return _chat_page(client)
    if module == "docs":
        return _docs_page(client)
    raise AssertionError(f"unknown module {module}")


def test_header_search_targets_active_module(authed_client, app):
    """U7.7: action + placeholder + body marker change per module."""
    client, _user_id, account_id = authed_client
    expected = {
        "mail": ("/app/mail/search", "Search mail — try from:"),
        "contacts": ("/app/contacts/", "Search contacts by name, email, or phone"),
        "calendar": ("/app/calendar/search", "Search calendar events"),
        "chat": ("#", "Filter conversations"),
        "docs": ("/app/docs/", "Search documents by name"),
    }
    for module, (action, placeholder) in expected.items():
        html = _render(client, app, account_id, module)
        assert f'data-active-module="{module}"' in html, module
        # Desktop + mobile header forms both target the module action.
        assert html.count(f'action="{action}"') >= 2, module
        assert placeholder in html, module


def test_header_search_panel_per_module(authed_client, app):
    """U7.5/U7.7: advanced panel rendered for every module except chat."""
    client, _user_id, account_id = authed_client
    panel_actions = {
        "mail": "/app/mail/search",
        "contacts": "/app/contacts/",
        "calendar": "/app/calendar/search",
        "docs": "/app/docs/",
    }
    for module, action in panel_actions.items():
        html = _render(client, app, account_id, module)
        assert 'id="search-options-panel"' in html, module
        assert "data-search-options-toggle" in html, module
        # The panel form targets the module endpoint (mail + module panels).
        assert f'action="{action}"' in html, module

    chat_html = _render(client, app, account_id, "chat")
    assert 'id="search-options-panel"' not in chat_html
    assert "data-search-options-toggle" not in chat_html


def test_chat_header_search_is_live(authed_client, app):
    """U7.11: chat search filters client-side — no navigation, no account_id."""
    client, _user_id, account_id = authed_client
    html = _render(client, app, account_id, "chat")
    assert 'data-search-live="1"' in html
    assert html.count("data-mail-search-form") >= 2


def test_mail_panel_carries_parse_and_folders_urls(authed_client, app):
    """U7.5: the mail panel keeps the grammar parse + folder endpoints."""
    client, _user_id, account_id = authed_client
    html = _render(client, app, account_id, "mail")
    assert 'data-parse-url="/app/mail/search/parse"' in html
    assert 'data-folders-url="/app/mail/search/folders"' in html


def test_launcher_highlights_active_module(authed_client, app):
    """U7.7: the app launcher marks the current module."""
    client, _user_id, account_id = authed_client
    active_cls = "bg-slate-100 font-medium text-slate-900"
    for module, href in {
        "contacts": "/app/contacts/",
        "calendar": "/app/calendar/",
        "docs": "/app/docs/",
    }.items():
        html = _render(client, app, account_id, module)
        assert f'<a href="{href}" class="flex items-center gap-3 px-3 py-2.5 {active_cls}"' in html
