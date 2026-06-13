import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from managers.dovecot import DovecotManager
from managers.postfix import PostfixManager


class InMemoryDovecotManager:
    def __init__(self, **kwargs):
        self._users: dict[str, dict] = {}

    def list_users(self, domain=""):
        result = []
        for email in sorted(self._users.keys()):
            if domain and not email.endswith(f"@{domain}"):
                continue
            result.append({"email": email})
        return result

    def user_exists(self, email):
        return email in self._users

    def add_user(self, email, password, quota_bytes=None):
        if email in self._users:
            raise FileExistsError(f"User {email} already exists")
        self._users[email] = {"email": email, "password": password, "quota_bytes": quota_bytes}

    def remove_user(self, email):
        if email not in self._users:
            raise FileNotFoundError(f"User {email} not found")
        del self._users[email]

    def set_password(self, email, password):
        if email not in self._users:
            raise FileNotFoundError(f"User {email} not found")
        self._users[email]["password"] = password

    def set_quota(self, email, quota_bytes):
        if email not in self._users:
            raise FileNotFoundError(f"User {email} not found")
        self._users[email]["quota_bytes"] = quota_bytes


class InMemoryPostfixManager:
    def __init__(self, **kwargs):
        self._domains: list[str] = []

    def list_domains(self):
        return [{"domain": d} for d in sorted(self._domains)]

    def add_domain(self, domain):
        if domain not in self._domains:
            self._domains.append(domain)

    def remove_domain(self, domain):
        if domain in self._domains:
            self._domains.remove(domain)


@pytest.fixture()
def mail_api_app():
    os.environ.setdefault("APP_DATABASE_URI", "sqlite://")
    os.environ.setdefault("SECRET_KEY", "test-secret-key")

    import server as server_module
    from server import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["MAIL_API_KEY"] = "test-api-key"
    server_module.API_KEY = "test-api-key"

    with patch.object(server_module, "dovecot", InMemoryDovecotManager()):
        with patch.object(server_module, "postfix", InMemoryPostfixManager()):
            yield flask_app


@pytest.fixture()
def mail_api_client(mail_api_app):
    return mail_api_app.test_client()


@pytest.fixture()
def auth_headers():
    return {"Authorization": "Bearer test-api-key", "Content-Type": "application/json"}


def test_health(mail_api_client):
    resp = mail_api_client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "mail-api"


def test_auth_required_add_domain(mail_api_client):
    resp = mail_api_client.post("/api/domains", json={"domain": "test.com"})
    assert resp.status_code == 401


def test_auth_required_add_user(mail_api_client):
    resp = mail_api_client.post("/api/users", json={"email": "a@b.com", "password": "x"})
    assert resp.status_code == 401


def test_auth_invalid_key(mail_api_client):
    headers = {"Authorization": "Bearer wrong-key", "Content-Type": "application/json"}
    resp = mail_api_client.post("/api/domains", json={"domain": "test.com"}, headers=headers)
    assert resp.status_code == 401


def test_add_domain(mail_api_client, auth_headers):
    resp = mail_api_client.post("/api/domains", json={"domain": "example.com"}, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["domain"] == "example.com"


def test_add_domain_missing(mail_api_client, auth_headers):
    resp = mail_api_client.post("/api/domains", json={}, headers=auth_headers)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"]["code"] == "VALIDATION_ERROR"


def test_add_domain_empty(mail_api_client, auth_headers):
    resp = mail_api_client.post("/api/domains", json={"domain": ""}, headers=auth_headers)
    assert resp.status_code == 400


def test_list_domains_empty(mail_api_client, auth_headers):
    resp = mail_api_client.get("/api/domains", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["data"] == []


def test_list_domains_after_add(mail_api_client, auth_headers):
    mail_api_client.post("/api/domains", json={"domain": "a.com"}, headers=auth_headers)
    mail_api_client.post("/api/domains", json={"domain": "b.com"}, headers=auth_headers)
    resp = mail_api_client.get("/api/domains", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    domains = [d["domain"] for d in data["data"]]
    assert "a.com" in domains
    assert "b.com" in domains


def test_remove_domain(mail_api_client, auth_headers):
    mail_api_client.post("/api/domains", json={"domain": "example.com"}, headers=auth_headers)
    resp = mail_api_client.delete("/api/domains/example.com", headers=auth_headers)
    assert resp.status_code == 200


def test_remove_domain_not_found(mail_api_client, auth_headers):
    resp = mail_api_client.delete("/api/domains/nonexistent.com", headers=auth_headers)
    assert resp.status_code == 200


def test_add_domain_idempotent(mail_api_client, auth_headers):
    mail_api_client.post("/api/domains", json={"domain": "idem.com"}, headers=auth_headers)
    resp = mail_api_client.post("/api/domains", json={"domain": "idem.com"}, headers=auth_headers)
    assert resp.status_code == 201


def test_add_user(mail_api_client, auth_headers):
    mail_api_client.post("/api/domains", json={"domain": "test.com"}, headers=auth_headers)
    resp = mail_api_client.post(
        "/api/users",
        json={"email": "user@test.com", "password": "secret123"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["email"] == "user@test.com"


def test_add_user_missing_email(mail_api_client, auth_headers):
    resp = mail_api_client.post("/api/users", json={"password": "x"}, headers=auth_headers)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"]["code"] == "VALIDATION_ERROR"


def test_add_user_missing_password(mail_api_client, auth_headers):
    resp = mail_api_client.post("/api/users", json={"email": "a@b.com"}, headers=auth_headers)
    assert resp.status_code == 400


def test_add_user_invalid_email(mail_api_client, auth_headers):
    resp = mail_api_client.post("/api/users", json={"email": "no-at-sign", "password": "x"}, headers=auth_headers)
    assert resp.status_code == 400
    data = resp.get_json()
    assert "email must contain @" in data["error"]["message"]


def test_add_user_duplicate(mail_api_client, auth_headers):
    mail_api_client.post("/api/users", json={"email": "dup@test.com", "password": "x"}, headers=auth_headers)
    resp = mail_api_client.post("/api/users", json={"email": "dup@test.com", "password": "y"}, headers=auth_headers)
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["error"]["code"] == "USER_EXISTS"


def test_list_users_empty(mail_api_client, auth_headers):
    resp = mail_api_client.get("/api/users", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["data"] == []


def test_list_users_after_add(mail_api_client, auth_headers):
    mail_api_client.post("/api/users", json={"email": "a@test.com", "password": "x"}, headers=auth_headers)
    mail_api_client.post("/api/users", json={"email": "b@test.com", "password": "y"}, headers=auth_headers)
    resp = mail_api_client.get("/api/users", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    emails = [u["email"] for u in data["data"]]
    assert "a@test.com" in emails
    assert "b@test.com" in emails


def test_list_users_filter_by_domain(mail_api_client, auth_headers):
    mail_api_client.post("/api/users", json={"email": "a@one.com", "password": "x"}, headers=auth_headers)
    mail_api_client.post("/api/users", json={"email": "b@two.com", "password": "y"}, headers=auth_headers)
    resp = mail_api_client.get("/api/users?domain=one.com", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    emails = [u["email"] for u in data["data"]]
    assert "a@one.com" in emails
    assert "b@two.com" not in emails


def test_remove_user(mail_api_client, auth_headers):
    mail_api_client.post("/api/users", json={"email": "rm@test.com", "password": "x"}, headers=auth_headers)
    resp = mail_api_client.delete("/api/users/rm@test.com", headers=auth_headers)
    assert resp.status_code == 200


def test_remove_user_not_found(mail_api_client, auth_headers):
    resp = mail_api_client.delete("/api/users/nope@test.com", headers=auth_headers)
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["error"]["code"] == "USER_NOT_FOUND"


def test_set_password(mail_api_client, auth_headers):
    mail_api_client.post("/api/users", json={"email": "pw@test.com", "password": "old"}, headers=auth_headers)
    resp = mail_api_client.put(
        "/api/users/pw@test.com/password",
        json={"password": "new"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


def test_set_password_missing(mail_api_client, auth_headers):
    mail_api_client.post("/api/users", json={"email": "pw2@test.com", "password": "old"}, headers=auth_headers)
    resp = mail_api_client.put("/api/users/pw2@test.com/password", json={}, headers=auth_headers)
    assert resp.status_code == 400


def test_set_password_not_found(mail_api_client, auth_headers):
    resp = mail_api_client.put(
        "/api/users/nope@test.com/password",
        json={"password": "new"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_check_user_exists(mail_api_client, auth_headers):
    mail_api_client.post("/api/users", json={"email": "chk@test.com", "password": "x"}, headers=auth_headers)
    resp = mail_api_client.get("/api/users/chk@test.com/check", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["exists"] is True


def test_check_user_not_found(mail_api_client, auth_headers):
    resp = mail_api_client.get("/api/users/nope@test.com/check", headers=auth_headers)
    assert resp.status_code == 404


def test_add_user_email_normalized(mail_api_client, auth_headers):
    resp = mail_api_client.post(
        "/api/users",
        json={"email": "User@Test.COM", "password": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["email"] == "user@test.com"
