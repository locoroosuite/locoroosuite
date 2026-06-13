import os
import sys
import tempfile
import importlib.util

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "mail-api"))

_spec = importlib.util.spec_from_file_location(
    "_test_mail_api_helpers",
    os.path.join(os.path.dirname(__file__), "test_mail_api.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
InMemoryDovecotManager = _mod.InMemoryDovecotManager
InMemoryPostfixManager = _mod.InMemoryPostfixManager
del _spec, _mod

from unittest.mock import patch


@pytest.fixture()
def mail_api_app():
    os.environ.setdefault("APP_DATABASE_URI", "sqlite://")
    os.environ.setdefault("SECRET_KEY", "test-secret-key")

    import server as server_module
    from server import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["MAIL_API_KEY"] = "test-api-key"
    server_module.API_KEY = "test-api-key"

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    flask_app.config["SENDING_LIMITS_DB"] = tmp.name
    server_module.SENDING_LIMITS_DB = tmp.name

    with patch.object(server_module, "dovecot", InMemoryDovecotManager()):
        with patch.object(server_module, "postfix", InMemoryPostfixManager()):
            yield flask_app

    os.unlink(tmp.name)


@pytest.fixture()
def client(mail_api_app):
    return mail_api_app.test_client()


@pytest.fixture()
def auth_headers():
    return {"Authorization": "Bearer test-api-key", "Content-Type": "application/json"}


def test_add_user_with_quota(client, auth_headers):
    resp = client.post("/api/users", json={
        "email": "user@example.com",
        "password": "secret",
        "quota_bytes": 5368709120,
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["email"] == "user@example.com"


def test_add_user_without_quota(client, auth_headers):
    resp = client.post("/api/users", json={
        "email": "user2@example.com",
        "password": "secret",
    }, headers=auth_headers)
    assert resp.status_code == 201


def test_set_quota(client, auth_headers):
    client.post("/api/users", json={"email": "q@example.com", "password": "x"}, headers=auth_headers)
    resp = client.put("/api/users/q@example.com/quota", json={"quota_bytes": 10737418240}, headers=auth_headers)
    assert resp.status_code == 200


def test_set_quota_user_not_found(client, auth_headers):
    resp = client.put("/api/users/noone@example.com/quota", json={"quota_bytes": 1000}, headers=auth_headers)
    assert resp.status_code == 404


def test_set_quota_invalid(client, auth_headers):
    client.post("/api/users", json={"email": "q2@example.com", "password": "x"}, headers=auth_headers)
    resp = client.put("/api/users/q2@example.com/quota", json={"quota_bytes": -1}, headers=auth_headers)
    assert resp.status_code == 400


def test_set_sending_limit(client, auth_headers):
    client.post("/api/users", json={"email": "sl@example.com", "password": "x"}, headers=auth_headers)
    resp = client.post("/api/users/sl@example.com/sending-limit", json={"max_per_day": 200}, headers=auth_headers)
    assert resp.status_code == 201


def test_get_sending_limit(client, auth_headers):
    client.post("/api/users", json={"email": "gl@example.com", "password": "x"}, headers=auth_headers)
    client.post("/api/users/gl@example.com/sending-limit", json={"max_per_day": 100}, headers=auth_headers)
    resp = client.get("/api/users/gl@example.com/sending-limit", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["max_per_day"] == 100
    assert data["sent_today"] == 0


def test_get_sending_limit_not_found(client, auth_headers):
    resp = client.get("/api/users/none@example.com/sending-limit", headers=auth_headers)
    assert resp.status_code == 404


def test_delete_sending_limit(client, auth_headers):
    client.post("/api/users", json={"email": "dl@example.com", "password": "x"}, headers=auth_headers)
    client.post("/api/users/dl@example.com/sending-limit", json={"max_per_day": 50}, headers=auth_headers)
    resp = client.delete("/api/users/dl@example.com/sending-limit", headers=auth_headers)
    assert resp.status_code == 200
    resp = client.get("/api/users/dl@example.com/sending-limit", headers=auth_headers)
    assert resp.status_code == 404


def test_sending_limit_invalid_max(client, auth_headers):
    resp = client.post("/api/users/x@example.com/sending-limit", json={"max_per_day": -5}, headers=auth_headers)
    assert resp.status_code == 400
