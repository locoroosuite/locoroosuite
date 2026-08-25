import json

import pytest

from app.shared.db import db as _db
from app.shared.models.core import CustomerSettings


def _read_settings(app, user_id):
    with app.app_context():
        _db.session.expire_all()
        return CustomerSettings.query.filter_by(customer_id=user_id).first()


def _post_pref(client, key, value):
    return client.post(
        "/app/mail/settings/pref",
        data=json.dumps({"key": key, "value": value}),
        content_type="application/json",
    )


def test_settings_page_renders(authed_client):
    client, _user_id, account_id = authed_client
    resp = client.get("/app/mail/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for marker in (
        'id="general"',
        'id="mail"',
        'id="protection"',
        'id="notifications"',
        'id="security"',
        'id="danger"',
        'data-pref="timezone"',
        'data-pref="theme"',
        'data-pref="sort_order"',
        'data-pref="preview_pane_default"',
        'data-pref="polling_interval"',
        'data-pref="protect_starred"',
        f'data-pref="spam_action_{account_id}"',
        f'data-pref="locked_keyword_{account_id}"',
        'data-pref="push_detailed"',
    ):
        assert marker in body, marker


def test_settings_page_requires_login(client):
    resp = client.get("/app/mail/settings")
    assert resp.status_code == 302


@pytest.mark.parametrize(
    "key,value,attr,expected",
    [
        ("polling_interval", 120, "polling_interval", 120),
        ("sort_order", "date_asc", "sort_order", "date_asc"),
        ("timezone", "America/New_York", "timezone", "America/New_York"),
        ("timezone", "browser", "timezone", "browser"),
        ("theme", "dark", "theme", "dark"),
        ("preview_pane_default", True, "preview_pane_default", True),
        ("protect_starred", False, "protect_starred", False),
        ("push_detailed", True, "push_detailed", True),
    ],
)
def test_pref_saves_fields(authed_client, app, key, value, attr, expected):
    client, user_id, _account_id = authed_client
    resp = _post_pref(client, key, value)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "saved"
    settings = _read_settings(app, user_id)
    assert getattr(settings, attr) == expected


@pytest.mark.parametrize(
    "key,value",
    [
        ("polling_interval", 45),
        ("polling_interval", None),
        ("polling_interval", "abc"),
        ("sort_order", "bogus"),
        ("timezone", "Mars/Olympus"),
        ("theme", "blue"),
        ("totally_unknown", True),
    ],
)
def test_pref_rejects_invalid_values(authed_client, key, value):
    client, _user_id, _account_id = authed_client
    resp = _post_pref(client, key, value)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"]["code"]


def test_pref_requires_missing_key(authed_client):
    client, _user_id, _account_id = authed_client
    resp = client.post(
        "/app/mail/settings/pref",
        data=json.dumps({"value": True}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_pref_requires_login(client):
    resp = client.post(
        "/app/mail/settings/pref",
        data=json.dumps({"key": "theme", "value": "dark"}),
        content_type="application/json",
    )
    assert resp.status_code == 302


def test_pref_spam_toggle_own_account(authed_client, app):
    client, user_id, account_id = authed_client
    resp = _post_pref(client, f"spam_action_{account_id}", False)
    assert resp.status_code == 200
    settings = _read_settings(app, user_id)
    assert settings is not None
    assert json.loads(settings.spam_action_prefs or "{}")[str(account_id)] is False


def test_pref_lock_toggle_own_account(authed_client, app):
    client, user_id, account_id = authed_client
    resp = _post_pref(client, f"locked_keyword_{account_id}", False)
    assert resp.status_code == 200
    settings = _read_settings(app, user_id)
    assert settings is not None
    assert json.loads(settings.locked_keyword_prefs or "{}")[str(account_id)] is False


@pytest.mark.parametrize("key", ["spam_action_999999", "locked_keyword_999999", "spam_action_abc"])
def test_pref_rejects_foreign_or_bad_account(authed_client, key):
    client, _user_id, _account_id = authed_client
    resp = _post_pref(client, key, True)
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_reset_cache(authed_client):
    client, _user_id, _account_id = authed_client
    from unittest.mock import patch

    with (
        patch(
            "app.modules.mail.controllers.settings.build_cache_path",
            return_value="/tmp/test_cache.db",
        ),
        patch("app.modules.mail.controllers.settings.purge_cache") as mock_purge,
    ):
        resp = client.post("/app/mail/settings/reset-cache")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    mock_purge.assert_called_once()


def test_reset_cache_no_account(client):
    resp = client.post("/app/mail/settings/reset-cache")
    assert resp.status_code == 302
