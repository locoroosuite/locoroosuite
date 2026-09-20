"""Tests for the push key store (U24.29): wrapping, lifecycle, fail-early config."""

import base64
import os

import pytest

from app.shared import push_keys
from app.shared.db import db
from app.shared.keys import clear_user_key, set_user_key
from app.shared.models.core import PushKeyStore, PushSubscription


@pytest.fixture(autouse=True)
def _isolated_fernet(monkeypatch, tmp_path):
    """Each test gets its own keywrap secret file."""
    monkeypatch.delenv(push_keys.ENV_SECRET, raising=False)
    monkeypatch.delenv(push_keys.ENV_KEY_PATH, raising=False)
    push_keys._reset_fernet_cache()
    yield str(tmp_path / "push_keywrap.key")
    push_keys._reset_fernet_cache()


def _make_subscription(user_id, endpoint="https://push.example.com/sub/1"):
    row = PushSubscription()
    row.user_id = user_id
    row.endpoint = endpoint
    row.p256dh = "p256dh-key"
    row.auth = "auth-key"
    row.user_agent = "pytest-agent"
    db.session.add(row)
    db.session.commit()
    return row


class TestWrapRoundtrip:
    def test_wrap_unwrap_roundtrip(self):
        dek = "ab" * 32
        wrapped = push_keys.wrap_dek(dek)
        assert isinstance(wrapped, bytes)
        assert push_keys.unwrap_dek(wrapped) == dek

    def test_auto_generates_key_file(self, app, tmp_path, monkeypatch):
        target = tmp_path / "keys" / "push_keywrap.key"
        monkeypatch.setenv(push_keys.ENV_KEY_PATH, str(target))
        wrapped = push_keys.wrap_dek("cd" * 32)
        assert target.exists()
        assert (os.stat(target).st_mode & 0o777) == 0o600
        assert push_keys.unwrap_dek(wrapped) == "cd" * 32

    def test_env_secret_used_when_valid(self, app, monkeypatch):
        raw = os.urandom(32)
        monkeypatch.setenv(push_keys.ENV_SECRET, base64.urlsafe_b64encode(raw).decode())
        wrapped = push_keys.wrap_dek("ef" * 32)
        assert push_keys.unwrap_dek(wrapped) == "ef" * 32

    def test_invalid_env_secret_is_fail_early(self, app, monkeypatch):
        monkeypatch.setenv(push_keys.ENV_SECRET, "not-base64-$$$")
        with pytest.raises(push_keys.PushKeyConfigError):
            push_keys.wrap_dek("aa" * 32)

    def test_wrong_length_env_secret_is_fail_early(self, app, monkeypatch):
        raw = os.urandom(16)
        monkeypatch.setenv(push_keys.ENV_SECRET, base64.urlsafe_b64encode(raw).decode())
        with pytest.raises(push_keys.PushKeyConfigError):
            push_keys.wrap_dek("aa" * 32)

    def test_unwrap_with_wrong_secret_is_actionable_error(self, app, monkeypatch):
        wrapped = push_keys.wrap_dek("ab" * 32)
        raw = os.urandom(32)
        monkeypatch.setenv(push_keys.ENV_SECRET, base64.urlsafe_b64encode(raw).decode())
        push_keys._reset_fernet_cache()
        with pytest.raises(push_keys.PushKeyConfigError, match="PUSH_KEYWRAP_SECRET"):
            push_keys.unwrap_dek(wrapped)


class TestStoreLifecycle:
    def test_store_requires_subscription(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            assert push_keys.store_push_dek_if_subscribed(user_id) is False
            assert db.session.query(PushKeyStore).count() == 0

    def test_store_and_ensure(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            _make_subscription(user_id)
            assert push_keys.store_push_dek_if_subscribed(user_id) is True
            row = db.session.get(PushKeyStore, user_id)
            assert row is not None
            # Simulate process restart: key gone from memory, reload from store.
            clear_user_key(user_id)
            assert push_keys.ensure_push_key(user_id) == "0" * 64
            # Second call uses the in-memory key.
            assert push_keys.ensure_push_key(user_id) == "0" * 64

    def test_store_updates_existing_row_on_rotation(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            _make_subscription(user_id)
            push_keys.store_push_dek_if_subscribed(user_id)
            set_user_key(user_id, "ff" * 32)
            push_keys.store_push_dek_if_subscribed(user_id)
            clear_user_key(user_id)
            assert push_keys.ensure_push_key(user_id) == "ff" * 32
            assert db.session.query(PushKeyStore).count() == 1

    def test_clear_removes_row(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            _make_subscription(user_id)
            push_keys.store_push_dek_if_subscribed(user_id)
            assert push_keys.clear_push_dek(user_id) is True
            assert push_keys.clear_push_dek(user_id) is False
            assert db.session.get(PushKeyStore, user_id) is None
            clear_user_key(user_id)
            assert push_keys.ensure_push_key(user_id) is None

    def test_ensure_without_row_returns_none(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            clear_user_key(user_id)
            assert push_keys.ensure_push_key(user_id) is None
