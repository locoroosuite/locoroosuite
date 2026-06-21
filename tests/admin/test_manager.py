import pytest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import User, Domain, ManagerDomain, CustomerAccount


@pytest.fixture()
def manager_client(app, client, _clean_db):
    manager_id = None
    domain_id = None
    with app.app_context():
        admin = User(
            email="admin@example.com",
            role="admin",
            is_active=True,
            password_hash=generate_password_hash("admin123"),
        )
        db.session.add(admin)
        db.session.flush()

        domain = Domain(
            name="example.com",
            is_active=True,
            status="complete",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_tls_mode="starttls",
        )
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id

        manager = User(
            email="mgr@example.com",
            role="manager",
            is_active=True,
            password_hash=generate_password_hash("mgr123"),
        )
        db.session.add(manager)
        db.session.flush()
        manager_id = manager.id

        link = ManagerDomain(manager_id=manager_id, domain_id=domain_id)
        db.session.add(link)
        db.session.commit()

    with client.session_transaction() as sess:
        sess["role"] = "manager"
        sess["user_id"] = manager_id

    yield client, manager_id, domain_id


def test_manager_dashboard(manager_client):
    client, _, _ = manager_client
    resp = client.get("/admin/manager/")
    assert resp.status_code == 200


@patch("app.admin.controllers.manager.log_audit")
def test_manager_create_customer(mock_audit, manager_client):
    client, _, _ = manager_client
    resp = client.post("/admin/manager/customers/new", data={"email": "newcust@example.com"})
    assert resp.status_code == 302


@patch("app.admin.controllers.manager.log_audit")
def test_manager_toggle_customer(mock_audit, manager_client, app):
    client, _, domain_id = manager_client
    cust_id = None
    with app.app_context():
        cust = User(email="toggle-cust@example.com", role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id

        account = CustomerAccount(
            customer_id=cust_id,
            domain_id=domain_id,
            email_address="toggle-cust@example.com",
            auth_type="password",
            username="toggle-cust@example.com",
            cache_db_path="",
        )
        db.session.add(account)
        db.session.commit()

    resp = client.post(f"/admin/manager/customers/{cust_id}/toggle")
    assert resp.status_code == 302


@patch("app.admin.controllers.manager.log_audit")
@patch("app.admin.controllers.manager.purge_cache")
def test_manager_purge_customer(mock_purge, mock_audit, manager_client, app):
    client, _, domain_id = manager_client
    cust_id = None
    with app.app_context():
        cust = User(email="purge-cust@example.com", role="customer", is_active=True)
        db.session.add(cust)
        db.session.flush()
        cust_id = cust.id

        account = CustomerAccount(
            customer_id=cust_id,
            domain_id=domain_id,
            email_address="purge-cust@example.com",
            auth_type="password",
            username="purge-cust@example.com",
            cache_db_path="/tmp/test-cache.db",
        )
        db.session.add(account)
        db.session.commit()

    resp = client.post(f"/admin/manager/customers/{cust_id}/purge")
    assert resp.status_code == 302
