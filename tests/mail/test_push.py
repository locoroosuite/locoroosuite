"""Tests for Web Push (U24.16 - U24.22): endpoints, VAPID config, sender, events, migration."""

import json
import sqlite3
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.shared import events
from app.shared import push as push_mod
from app.shared.app_migrations import APP_DB_MIGRATIONS
from app.shared.db import db
from app.shared.migrations import has_table, run_migrations, table_columns
from app.shared.models.core import PushSubscription, PushVapidKey


def _make_subscription(user_id, endpoint="https://push.example.com/sub/1", disabled=False):
    row = PushSubscription()
    row.user_id = user_id
    row.endpoint = endpoint
    row.p256dh = "p256dh-key"
    row.auth = "auth-key"
    row.user_agent = "pytest-agent"
    if disabled:
        row.disabled_at = datetime.now(UTC)
    db.session.add(row)
    db.session.commit()
    return row


class TestPushKeyEndpoint:
    def test_returns_generated_key_and_persists(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.get("/app/mail/push/key")
        assert resp.status_code == 200
        key = resp.get_json()["public_key"]
        assert isinstance(key, str) and len(key) > 20
        with client.application.app_context():
            assert db.session.query(PushVapidKey).count() == 1

    def test_requires_customer(self, client):
        resp = client.get("/app/mail/push/key")
        assert resp.status_code == 302

    def test_half_env_config_is_fail_early(self, app, authed_client, monkeypatch):
        client, _user_id, _ = authed_client
        monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "only-public")
        monkeypatch.delenv("PUSH_VAPID_PRIVATE_KEY", raising=False)
        resp = client.get("/app/mail/push/key")
        assert resp.status_code == 503
        assert "misconfigured" in resp.get_json()["error"]["message"].lower()

    def test_env_override_wins(self, app, authed_client, monkeypatch):
        client, _user_id, _ = authed_client
        monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "env-pub")
        monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "env-priv")
        resp = client.get("/app/mail/push/key")
        assert resp.status_code == 200
        assert resp.get_json()["public_key"] == "env-pub"
        with client.application.app_context():
            assert db.session.query(PushVapidKey).count() == 0


class TestPushSubscribe:
    def test_happy_path(self, authed_client):
        client, user_id, _ = authed_client
        resp = client.post(
            "/app/mail/push/subscribe",
            data=json.dumps(
                {
                    "endpoint": "https://push.example.com/sub/abc",
                    "keys": {"p256dh": "pub", "auth": "authval"},
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "subscribed"
        with client.application.app_context():
            row = PushSubscription.query.filter_by(user_id=user_id).one()
            assert row.endpoint == "https://push.example.com/sub/abc"
            assert row.disabled_at is None

    def test_missing_fields(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.post(
            "/app/mail/push/subscribe",
            data=json.dumps({"endpoint": "https://push.example.com/sub/abc"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_oversized_endpoint(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.post(
            "/app/mail/push/subscribe",
            data=json.dumps(
                {
                    "endpoint": "https://push.example.com/" + "x" * 600,
                    "keys": {"p256dh": "pub", "auth": "authval"},
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_resubscribe_reactivates(self, authed_client):
        client, user_id, _ = authed_client
        with client.application.app_context():
            _make_subscription(user_id, endpoint="https://push.example.com/sub/abc", disabled=True)
        resp = client.post(
            "/app/mail/push/subscribe",
            data=json.dumps(
                {
                    "endpoint": "https://push.example.com/sub/abc",
                    "keys": {"p256dh": "new", "auth": "new"},
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        with client.application.app_context():
            row = PushSubscription.query.filter_by(user_id=user_id).one()
            assert row.disabled_at is None
            assert row.p256dh == "new"

    def test_requires_customer(self, client):
        resp = client.post(
            "/app/mail/push/subscribe",
            data=json.dumps({"endpoint": "x", "keys": {"p256dh": "p", "auth": "a"}}),
            content_type="application/json",
        )
        assert resp.status_code == 302


class TestPushUnsubscribe:
    def test_happy_path(self, authed_client):
        client, user_id, _ = authed_client
        with client.application.app_context():
            _make_subscription(user_id, endpoint="https://push.example.com/sub/gone")
        resp = client.post(
            "/app/mail/push/unsubscribe",
            data=json.dumps({"endpoint": "https://push.example.com/sub/gone"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        with client.application.app_context():
            assert PushSubscription.query.filter_by(user_id=user_id).count() == 0

    def test_unknown_endpoint_404(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.post(
            "/app/mail/push/unsubscribe",
            data=json.dumps({"endpoint": "https://push.example.com/nope"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_missing_endpoint_400(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.post(
            "/app/mail/push/unsubscribe",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestPushDevices:
    def test_lists_devices(self, authed_client):
        client, user_id, _ = authed_client
        with client.application.app_context():
            _make_subscription(user_id)
        resp = client.get("/app/mail/push/devices")
        assert resp.status_code == 200
        devices = resp.get_json()["devices"]
        assert len(devices) == 1
        assert devices[0]["user_agent"] == "pytest-agent"
        assert devices[0]["created_at"]

    def test_remove_device(self, authed_client):
        client, user_id, _ = authed_client
        with client.application.app_context():
            row = _make_subscription(user_id)
            device_id = row.id
        resp = client.post(f"/app/mail/push/devices/{device_id}/remove")
        assert resp.status_code == 200
        with client.application.app_context():
            assert PushSubscription.query.filter_by(user_id=user_id).count() == 0

    def test_remove_unknown_device_404(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.post("/app/mail/push/devices/999999/remove")
        assert resp.status_code == 404

    def test_empty_state(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.get("/app/mail/push/devices")
        assert resp.status_code == 200
        assert resp.get_json()["devices"] == []


class TestPushDetailed:
    def test_toggle_on(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.post(
            "/app/mail/push/detailed",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["push_detailed"] is True

    def test_non_bool_rejected(self, authed_client):
        client, _user_id, _ = authed_client
        resp = client.post(
            "/app/mail/push/detailed",
            data=json.dumps({"enabled": "yes"}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestSendNewMailPush:
    def test_sends_only_to_active_subscriptions(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            _make_subscription(user_id, endpoint="https://push.example.com/a")
            _make_subscription(user_id, endpoint="https://push.example.com/b")
            _make_subscription(user_id, endpoint="https://push.example.com/c", disabled=True)
            with patch.object(push_mod, "_send_to_subscription") as send:
                push_mod.send_new_mail_push(app, user_id, 2, None)
            assert send.call_count == 2

    def test_no_subscriptions_is_noop(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            with patch.object(push_mod, "_send_to_subscription") as send:
                push_mod.send_new_mail_push(app, user_id, 1, None)
            send.assert_not_called()

    def test_generic_payload_by_default(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            _make_subscription(user_id)
            with patch("pywebpush.webpush") as wp:
                push_mod.send_new_mail_push(
                    app, user_id, 3, {"subject": "Secret", "sender": "a@b.c"}
                )
            payload = json.loads(wp.call_args.kwargs["data"])
            assert payload["title"] == "New email"
            assert "Secret" not in payload["body"]
            assert "3 new messages" in payload["body"]

    def test_detailed_payload_when_enabled(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            _make_subscription(user_id)
            from app.modules.mail.controllers.helpers import _get_or_create_settings

            settings = _get_or_create_settings(user_id)
            settings.push_detailed = True
            db.session.commit()
            with patch("pywebpush.webpush") as wp:
                push_mod.send_new_mail_push(
                    app, user_id, 2, {"subject": "Hello", "sender": "A <a@b.c>"}
                )
            payload = json.loads(wp.call_args.kwargs["data"])
            assert payload["title"] == "A <a@b.c>"
            assert payload["body"].startswith("Hello")

    def test_gone_subscription_is_disabled(self, app, authed_client):
        _client, user_id, _ = authed_client
        with app.app_context():
            row = _make_subscription(user_id)
            from pywebpush import WebPushException

            exc = WebPushException("gone")
            exc.response = MagicMock(status_code=410)
            with patch("pywebpush.webpush", side_effect=exc):
                push_mod.send_new_mail_push(app, user_id, 1, None)
            db.session.refresh(row)
            assert row.disabled_at is not None


class TestNewMailEventWiring:
    def test_new_mail_event_schedules_push(self, app, authed_client):
        _client, user_id, _ = authed_client
        with patch.object(push_mod, "_app_ref", app), patch.object(push_mod, "_executor") as ex:
            events.push_event(user_id, "new_mail", {"account_id": 1, "folder": "INBOX", "count": 3})
        assert ex.submit.call_count == 1
        assert ex.submit.call_args.args[2] == user_id
        assert ex.submit.call_args.args[3] == 3

    def test_other_events_do_not_schedule_push(self, app, authed_client):
        _client, user_id, _ = authed_client
        with patch.object(push_mod, "_app_ref", app), patch.object(push_mod, "_executor") as ex:
            events.push_event(user_id, "sync_status", {"state": "done"})
        ex.submit.assert_not_called()


class TestPushMigration:
    def test_creates_push_schema_on_legacy_db(self, tmp_path):
        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE customer_settings (customer_id INTEGER PRIMARY KEY)")
        conn.commit()
        applied = run_migrations(conn, APP_DB_MIGRATIONS)
        assert applied == len(APP_DB_MIGRATIONS)
        assert has_table(conn, "push_subscriptions")
        assert has_table(conn, "push_vapid_keys")
        assert "push_detailed" in table_columns(conn, "customer_settings")
        second = run_migrations(conn, APP_DB_MIGRATIONS)
        assert second == 0
        conn.close()

    def test_registry_contains_push_migration(self):
        assert any(m.name == "0013_push_notifications" for m in APP_DB_MIGRATIONS)


class TestVapidGeneration:
    def test_generated_keypair_is_valid_p256(self, app, authed_client, monkeypatch):
        client, _user_id, _ = authed_client
        monkeypatch.delenv("PUSH_VAPID_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("PUSH_VAPID_PRIVATE_KEY", raising=False)
        resp = client.get("/app/mail/push/key")
        assert resp.status_code == 200
        key = resp.get_json()["public_key"]
        import base64

        raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        assert len(raw) == 65  # uncompressed P-256 point
        assert raw[0] == 0x04
