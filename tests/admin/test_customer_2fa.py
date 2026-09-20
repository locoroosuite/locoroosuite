from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import TrustedDevice, User


def _seed_customer(app, email="cust@example.com", enable_2fa=True):
    cust_id = None
    with app.app_context():
        cust = User(email=email, role="customer", is_active=True)  # type: ignore[call-arg]
        if enable_2fa:
            cust.totp_secret = "JBSWY3DPEHPK3PXP"
            cust.totp_enabled = True
            cust.backup_codes = json.dumps([hashlib.sha256(b"TESTCODE1").hexdigest()])
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id
        db.session.commit()
    return cust_id


def _seed_admin(app, email="admin@example.com"):
    with app.app_context():
        admin = User()
        admin.email = email
        admin.role = "admin"
        admin.is_active = True
        admin.password_hash = generate_password_hash("admin123")
        db.session.add(admin)
        db.session.commit()


def _seed_trusted_device(app, user_id):
    with app.app_context():
        device = TrustedDevice()
        device.user_id = user_id
        device.token_hash = hashlib.sha256(b"raw-token").hexdigest()
        device.user_agent = "Mozilla/5.0 Chrome"
        device.ip_address = "127.0.0.1"
        device.created_at = datetime.now(UTC)
        device.expires_at = datetime.now(UTC) + timedelta(days=30)
        db.session.add(device)
        db.session.commit()


@patch("app.admin.controllers.customer_2fa.log_audit")
def test_disable_2fa_happy_path(mock_audit, admin_client, app):
    client, admin_id = admin_client
    cust_id = _seed_customer(app)
    _seed_trusted_device(app, cust_id)

    resp = client.post(f"/admin/customers/{cust_id}/disable-2fa")
    assert resp.status_code == 302

    with app.app_context():
        cust = db.session.get(User, cust_id)
        assert cust is not None
        assert cust.totp_enabled is False
        assert cust.totp_secret is None
        assert cust.backup_codes is None
        devices = TrustedDevice.query.filter_by(user_id=cust_id, revoked_at=None).all()
        assert devices == []
    mock_audit.assert_called_once()
    assert mock_audit.call_args[0][2] == "customer_2fa_disable"
    assert "customer=cust@example.com" in mock_audit.call_args[0][3]
    assert mock_audit.call_args[0][0] == admin_id


@patch("app.admin.controllers.customer_2fa.log_audit")
def test_disable_2fa_not_enabled_is_noop(mock_audit, admin_client, app):
    client, _ = admin_client
    cust_id = _seed_customer(app, email="plain@example.com", enable_2fa=False)

    resp = client.post(f"/admin/customers/{cust_id}/disable-2fa", follow_redirects=True)
    assert resp.status_code == 200
    assert b"2FA is not enabled" in resp.data

    with app.app_context():
        cust = db.session.get(User, cust_id)
        assert cust is not None
        assert cust.totp_enabled is False
        assert cust.totp_secret is None
    mock_audit.assert_not_called()


def test_disable_2fa_unknown_customer_404(admin_client, app):
    client, _ = admin_client
    resp = client.post("/admin/customers/999999/disable-2fa")
    assert resp.status_code == 404


def test_disable_2fa_requires_admin_role(client, app):
    _seed_admin(app)
    cust_id = _seed_customer(app)
    resp = client.post(f"/admin/customers/{cust_id}/disable-2fa")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]

    with app.app_context():
        cust = db.session.get(User, cust_id)
        assert cust is not None
        assert cust.totp_enabled is True


def test_disable_2fa_rejects_manager_role(client, app):
    _seed_admin(app)
    with app.app_context():
        manager = User(email="mgr@example.com", role="manager", is_active=True)  # type: ignore[call-arg]
        db.session.add(manager)
        db.session.flush()
        mgr_id = manager.id
        db.session.commit()

    cust_id = _seed_customer(app)
    with client.session_transaction() as sess:
        sess["role"] = "manager"
        sess["user_id"] = mgr_id

    resp = client.post(f"/admin/customers/{cust_id}/disable-2fa")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]

    with app.app_context():
        cust = db.session.get(User, cust_id)
        assert cust is not None
        assert cust.totp_enabled is True


def test_customers_page_shows_2fa_badge_and_action(admin_client, app):
    client, _ = admin_client
    enabled_id = _seed_customer(app, email="secured@example.com")
    plain_id = _seed_customer(app, email="unsecured@example.com", enable_2fa=False)

    resp = client.get("/admin/customers")
    assert resp.status_code == 200
    html = resp.data.decode()

    enabled_row = html[html.index("secured@example.com") : html.index("unsecured@example.com")]
    assert "Two-factor authentication is enabled" in enabled_row
    assert "Disable 2FA" in enabled_row
    assert f"/admin/customers/{enabled_id}/disable-2fa" in enabled_row

    tail = html[html.index("unsecured@example.com") :]
    assert f"/admin/customers/{plain_id}/disable-2fa" not in tail
    assert "Two-factor authentication is enabled" not in tail
