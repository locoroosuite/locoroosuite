"""Tests for push arming in the WorkerManager (U24.29/U24.30)."""

from unittest.mock import MagicMock, patch

from app.shared import push_keys
from app.shared.db import db
from app.shared.keys import clear_user_key, get_user_key, set_user_key
from app.shared.models.core import CustomerAccount, PushSubscription, User
from app.workers.manager import WorkerManager


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


def _new_manager(app):
    with (
        patch("app.workers.manager.WorkerManager._ensure_idle", MagicMock()),
        patch("app.workers.manager.WorkerManager.enqueue_sync", MagicMock()),
    ):
        wm = WorkerManager(app)
    return wm


def _patch_idle(wm):
    return patch.object(wm, "_ensure_idle", MagicMock()), patch.object(
        wm, "enqueue_sync", MagicMock()
    )


class TestArmPushUser:
    def test_arms_all_active_accounts(self, app, authed_client):
        _client, user_id, account_id = authed_client
        wm = _new_manager(app)
        with app.app_context():
            existing = db.session.get(CustomerAccount, account_id)
            if existing is None:
                raise AssertionError("fixture account missing")
            second = CustomerAccount()
            second.customer_id = user_id
            second.domain_id = existing.domain_id
            second.email_address = "second@example.com"
            second.auth_type = "password"
            second.username = "second@example.com"
            db.session.add(second)
            db.session.commit()
            _make_subscription(user_id)
            p1, p2 = _patch_idle(wm)
            with p1, p2:
                assert wm.arm_push_user(user_id) is True
            assert wm.is_push_armed(user_id)
            assert wm._push_armed_accounts == {account_id, second.id}

    def test_arm_without_key_returns_false(self, app, authed_client):
        _client, user_id, _ = authed_client
        wm = _new_manager(app)
        with app.app_context():
            _make_subscription(user_id)
            # No key in memory and no key store row: nothing to arm from.
            clear_user_key(user_id)
            p1, p2 = _patch_idle(wm)
            with p1, p2:
                assert wm.arm_push_user(user_id) is False
            assert not wm.is_push_armed(user_id)

    def test_disarm_removes_accounts(self, app, authed_client):
        _client, user_id, _account_id = authed_client
        wm = _new_manager(app)
        with app.app_context():
            _make_subscription(user_id)
            p1, p2 = _patch_idle(wm)
            with p1, p2:
                wm.arm_push_user(user_id)
                wm.disarm_push_user(user_id)
            assert not wm.is_push_armed(user_id)
            assert wm._push_armed_accounts == set()


class TestStartupArming:
    def test_loads_key_from_store(self, app, authed_client):
        _client, user_id, _ = authed_client
        wm = _new_manager(app)
        with app.app_context():
            _make_subscription(user_id)
            push_keys.store_push_dek_if_subscribed(user_id)
            clear_user_key(user_id)
            p1, p2 = _patch_idle(wm)
            with p1, p2:
                wm._arm_push_users()
            assert wm.is_push_armed(user_id)
            assert get_user_key(user_id) == "0" * 64

    def test_no_subscriptions_arms_nothing(self, app, authed_client):
        _client, user_id, _ = authed_client
        wm = _new_manager(app)
        with app.app_context():
            p1, p2 = _patch_idle(wm)
            with p1, p2:
                wm._arm_push_users()
            assert not wm.is_push_armed(user_id)

    def test_subscribed_user_without_key_store_row_is_skipped(self, app, authed_client, caplog):
        """Prod scenario (U24.29 gap): a subscription that predates the push
        key store has no wrapped DEK row. After a restart (in-memory keys are
        gone) startup arming must skip that user with a warning — and must
        still arm users that do have rows."""
        _client, user_id, _ = authed_client
        wm = _new_manager(app)
        with app.app_context():
            fixture_account = CustomerAccount.query.first()
            assert fixture_account is not None
            second_user = User()
            second_user.email = "second@example.com"
            second_user.role = "customer"
            second_user.is_active = True
            second_user.password_hash = "x"
            db.session.add(second_user)
            db.session.flush()
            second_account = CustomerAccount()
            second_account.customer_id = second_user.id
            second_account.domain_id = fixture_account.domain_id
            second_account.email_address = "second@example.com"
            second_account.auth_type = "password"
            second_account.username = "second@example.com"
            second_account.cache_db_path = ""
            db.session.add(second_account)
            db.session.commit()

            # User 1: subscribed, but no key-store row (pre-U24.29 subscription).
            _make_subscription(user_id)
            # User 2: subscribed, key-store row present.
            _make_subscription(second_user.id, endpoint="https://push.example.com/sub/2")
            set_user_key(second_user.id, "f" * 64)
            assert push_keys.store_push_dek_if_subscribed(second_user.id) is True

            # Simulate a restart: every in-memory DEK is gone.
            clear_user_key(user_id)
            clear_user_key(second_user.id)

            p1, p2 = _patch_idle(wm)
            with p1, p2, caplog.at_level("WARNING", logger="app.workers.manager"):
                wm._arm_push_users()
            assert not wm.is_push_armed(user_id)
            assert wm.is_push_armed(second_user.id)
            assert any(
                "no usable key" in record.message and f"user_id={user_id}" in record.message
                for record in caplog.records
            )


class TestClearActiveKeepsArmedIdle:
    def test_armed_account_idle_not_stopped_on_logout(self, app, authed_client):
        _client, user_id, account_id = authed_client
        wm = _new_manager(app)
        with app.app_context():
            _make_subscription(user_id)
            p1, p2 = _patch_idle(wm)
            with p1, p2:
                wm.arm_push_user(user_id)
                wm.set_active_account(user_id, account_id)
            with patch.object(wm, "_stop_idle_for_account", MagicMock()) as stop:
                wm.clear_active_customer(user_id)
            stop.assert_not_called()
            # Still armed for push even though the web session is gone.
            assert wm.is_push_armed(user_id)

    def test_non_armed_account_idle_stopped_on_logout(self, app, authed_client):
        _client, user_id, account_id = authed_client
        wm = _new_manager(app)
        with app.app_context():
            p1, p2 = _patch_idle(wm)
            with p1, p2:
                wm.set_active_account(user_id, account_id)
            with patch.object(wm, "_stop_idle_for_account", MagicMock()) as stop:
                wm.clear_active_customer(user_id)
            stop.assert_called_once_with(account_id)
