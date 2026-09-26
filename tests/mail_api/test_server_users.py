"""Endpoint tests for the mail-api Flask service against REAL file-backed managers.

Complements ``test_mail_api.py`` (in-memory managers) and
``test_quota_sending.py`` (sending limits / quota): these tests exercise the
production DovecotManager + PostfixManager on tmp paths, so they verify the
actual passwd-file bytes that Dovecot reads — including the atomic-rename
write path (see test_dovecot_manager.py for the direct regression tests).
"""

import pytest
import server
from managers.dovecot import DovecotManager

AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server,
        "dovecot",
        DovecotManager(
            users_path=str(tmp_path / "passwd"),
            mail_root=str(tmp_path / "vhosts"),
        ),
    )
    monkeypatch.setattr(server, "API_KEY", "test-key")
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _add_user(client, email: str = "alice@test.localhost", password: str = "Secret123!"):
    r = client.post("/api/users", headers=AUTH, json={"email": email, "password": password})
    assert r.status_code == 201, r.get_json()


def _passwd_lines(tmp_path) -> list[str]:
    return (tmp_path / "passwd").read_text().splitlines()


class TestRealFileBackedUsers:
    def test_add_user_writes_valid_passwd_line(self, client, tmp_path):
        _add_user(client)

        lines = _passwd_lines(tmp_path)
        assert len(lines) == 1
        email, hash_part, uid, gid, home = lines[0].split(":")
        assert email == "alice@test.localhost"
        assert hash_part.startswith("{SHA256-CRYPT}$5$")
        assert (uid, gid, home) == ("5000", "5000", "/var/mail/vhosts/%d/%n")

        r = client.get("/api/users/alice@test.localhost/check", headers=AUTH)
        assert r.status_code == 200
        assert r.get_json()["exists"] is True

    def test_add_duplicate_maps_to_409(self, client):
        _add_user(client)
        r = client.post(
            "/api/users",
            headers=AUTH,
            json={"email": "alice@test.localhost", "password": "Other456!"},
        )
        assert r.status_code == 409
        assert r.get_json()["error"]["code"] == "USER_EXISTS"

    def test_remove_user_deletes_only_that_line(self, client, tmp_path):
        _add_user(client, "alice@test.localhost")
        _add_user(client, "bob@test.localhost")

        r = client.delete("/api/users/alice@test.localhost", headers=AUTH)
        assert r.status_code == 200

        lines = _passwd_lines(tmp_path)
        assert len(lines) == 1
        assert lines[0].startswith("bob@test.localhost:")

    def test_set_password_rotates_hash_on_disk(self, client, tmp_path):
        _add_user(client)
        old_hash = _passwd_lines(tmp_path)[0].split(":")[1]

        r = client.put(
            "/api/users/alice@test.localhost/password",
            headers=AUTH,
            json={"password": "NewPass456!"},
        )
        assert r.status_code == 200

        new_hash = _passwd_lines(tmp_path)[0].split(":")[1]
        assert new_hash != old_hash
        assert new_hash.startswith("{SHA256-CRYPT}$5$")

    def test_no_temp_files_left_behind_after_mutations(self, client, tmp_path):
        _add_user(client, "alice@test.localhost")
        client.put(
            "/api/users/alice@test.localhost/password",
            headers=AUTH,
            json={"password": "NewPass456!"},
        )
        _add_user(client, "bob@test.localhost")
        client.delete("/api/users/alice@test.localhost", headers=AUTH)

        assert sorted(p.name for p in tmp_path.iterdir()) == ["passwd", "vhosts"]
