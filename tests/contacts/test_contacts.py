import json
import tempfile
import os
from unittest.mock import patch, MagicMock

import pytest

from app.shared.models.core import Domain


def _setup_test_env(app, account_id, with_carddav=True, with_cache=True):
    paths = {}
    with app.app_context():
        from app.shared.db import db
        from app.shared.models.core import CustomerAccount
        account = db.session.get(CustomerAccount, account_id)
        domain = db.session.get(Domain, account.domain_id)
        if with_carddav:
            domain.carddav_host = "localhost"
            domain.carddav_port = 5232
            domain.carddav_use_tls = False
        if with_cache:
            f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            paths["cache"] = f.name
            f.close()
            account.cache_db_path = paths["cache"]
        db.session.commit()
    return paths


def test_contact_list_no_carddav_config(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id, with_carddav=False)
    try:
        resp = client.get("/app/contacts/")
        assert resp.status_code == 200
        assert b"not configured" in resp.data
    finally:
        if paths.get("cache"):
            os.unlink(paths["cache"])


def test_contact_list_empty(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch("app.modules.contacts.controllers.contacts._sync_contacts"):
            resp = client.get("/app/contacts/")
        assert resp.status_code == 200
        assert b"No contacts yet" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_list_with_contacts(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, first_name, last_name, email_work, tel_cell) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-uid-1", "/test/1.vcf", "etag1", "Alice Smith", "Alice", "Smith", "alice@example.com", "+1234"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.contacts._sync_contacts"):
            resp = client.get("/app/contacts/")
        assert resp.status_code == 200
        assert b"Alice Smith" in resp.data
        assert b"alice@example.com" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_detail(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, first_name, last_name, email_work, org, title) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-uid-2", "/test/2.vcf", "etag2", "Bob Jones", "Bob", "Jones", "bob@example.com", "Acme Corp", "Engineer"),
        )
        conn.commit()
        conn.close()

    try:
        resp = client.get(f"/app/contacts/{account_id}/test-uid-2")
        assert resp.status_code == 200
        assert b"Bob Jones" in resp.data
        assert b"bob@example.com" in resp.data
        assert b"Acme Corp" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_detail_not_found(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get(f"/app/contacts/{account_id}/nonexistent")
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


def test_contact_new_get(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.get("/app/contacts/new")
        assert resp.status_code == 200
        assert b"New Contact" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_new_post_validation_error(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        resp = client.post("/app/contacts/new", data={"fn": "", "first_name": "", "last_name": ""})
        assert resp.status_code == 200
        assert b"Name is required" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_edit_get(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, first_name, last_name) VALUES (?, ?, ?, ?, ?, ?)",
            ("test-uid-3", "/test/3.vcf", "etag3", "Carol White", "Carol", "White"),
        )
        conn.commit()
        conn.close()

    try:
        resp = client.get(f"/app/contacts/{account_id}/test-uid-3/edit")
        assert resp.status_code == 200
        assert b"Edit Contact" in resp.data
        assert b"Carol" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_delete(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn) VALUES (?, ?, ?, ?)",
            ("test-uid-4", "/test/4.vcf", "etag4", "Dave Black"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.contacts.carddav") as mock_carddav:
            mock_session = MagicMock()
            mock_carddav.discover_address_book.return_value = (mock_session, "/ab/", "Contacts")
            mock_carddav.delete_contact.return_value = True
            resp = client.post(f"/app/contacts/{account_id}/test-uid-4/delete")
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])


def test_contact_search(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-a", "/a.vcf", "e1", "Alice Smith", "alice@example.com"),
        )
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-b", "/b.vcf", "e2", "Bob Jones", "bob@example.com"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.contacts._sync_contacts"):
            resp = client.get("/app/contacts/?q=Alice")
        assert resp.status_code == 200
        assert b"Alice Smith" in resp.data
        assert b"Bob Jones" not in resp.data
    finally:
        os.unlink(paths["cache"])


def test_api_search(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-api", "/api.vcf", "e1", "Api Testuser", "api@example.com"),
        )
        conn.commit()
        conn.close()

    try:
        resp = client.get("/app/contacts/api/search?q=api")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1
        assert data[0]["fn"] == "Api Testuser"
        assert any(e["email"] == "api@example.com" for e in data[0]["emails"])
    finally:
        os.unlink(paths["cache"])


def test_api_search_too_short(authed_client, app):
    client, user_id, account_id = authed_client
    resp = client.get("/app/contacts/api/search?q=a")
    assert resp.status_code == 200
    assert json.loads(resp.data) == []


def test_contact_sync(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)
    try:
        with patch("app.modules.contacts.controllers.contacts._sync_contacts") as mock_sync:
            resp = client.post("/app/contacts/sync")
        assert resp.status_code == 302
        mock_sync.assert_called_once()
    finally:
        os.unlink(paths["cache"])


def test_api_auto_save_new_recipient(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    try:
        with patch("app.modules.contacts.controllers.api._get_credentials", return_value="pw"), \
             patch("app.modules.contacts.controllers.api.carddav.discover_address_book") as mock_disc, \
             patch("app.modules.contacts.controllers.api.carddav.create_contact") as mock_create:
            mock_session = MagicMock()
            mock_disc.return_value = (mock_session, "/abook/", "Contacts")
            mock_create.return_value = ("/abook/new-uid.vcf", "etag-new")

            resp = client.post(
                "/app/contacts/api/auto-save",
                data=json.dumps({"account_id": account_id, "recipients": [{"email": "new@example.com", "name": "New User"}]}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["saved"] == 1
        assert data["skipped"] == 0
        assert data["failed"] == 0

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            rows = conn.execute("SELECT fn, email_work FROM contacts WHERE email_work = ?", ("new@example.com",)).fetchall()
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "New User"
    finally:
        os.unlink(paths["cache"])


def test_api_auto_save_existing_recipient(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-ex", "/ex.vcf", "e1", "Existing", "existing@example.com"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.api._get_credentials", return_value="pw"):
            resp = client.post(
                "/app/contacts/api/auto-save",
                data=json.dumps({"account_id": account_id, "recipients": [{"email": "existing@example.com", "name": "Existing"}]}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["saved"] == 0
        assert data["skipped"] == 1
    finally:
        os.unlink(paths["cache"])


def test_api_auto_save_no_carddav(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id, with_carddav=False)

    try:
        resp = client.post(
            "/app/contacts/api/auto-save",
            data=json.dumps({"account_id": account_id, "recipients": [{"email": "any@example.com"}]}),
            content_type="application/json",
        )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["saved"] == 0
        assert data["skipped"] == 0
        assert data["failed"] == 0
    finally:
        if paths.get("cache"):
            os.unlink(paths["cache"])


def test_api_auto_save_invalid_request(authed_client, app):
    client, user_id, account_id = authed_client

    resp = client.post(
        "/app/contacts/api/auto-save",
        data="not json",
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["saved"] == 0


def test_api_auto_save_empty_recipients(authed_client, app):
    client, user_id, account_id = authed_client

    resp = client.post(
        "/app/contacts/api/auto-save",
        data=json.dumps({"account_id": account_id, "recipients": []}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["saved"] == 0


def test_api_auto_save_carddav_down(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    try:
        with patch("app.modules.contacts.controllers.api._get_credentials", return_value="pw"), \
             patch("app.modules.contacts.controllers.api.carddav.discover_address_book", side_effect=Exception("connection refused")):
            resp = client.post(
                "/app/contacts/api/auto-save",
                data=json.dumps({"account_id": account_id, "recipients": [{"email": "down@example.com"}]}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["saved"] == 0
        assert data["failed"] == 1
    finally:
        os.unlink(paths["cache"])


def test_api_auto_save_no_name(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    try:
        with patch("app.modules.contacts.controllers.api._get_credentials", return_value="pw"), \
             patch("app.modules.contacts.controllers.api.carddav.discover_address_book") as mock_disc, \
             patch("app.modules.contacts.controllers.api.carddav.create_contact") as mock_create:
            mock_session = MagicMock()
            mock_disc.return_value = (mock_session, "/abook/", "Contacts")
            mock_create.return_value = ("/abook/nn.vcf", "etag-nn")

            resp = client.post(
                "/app/contacts/api/auto-save",
                data=json.dumps({"account_id": account_id, "recipients": [{"email": "jane@company.org"}]}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["saved"] == 1

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            rows = conn.execute("SELECT fn, email_work FROM contacts WHERE email_work = ?", ("jane@company.org",)).fetchall()
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "jane"
    finally:
        os.unlink(paths["cache"])


@pytest.mark.parametrize("invalid_email", [
    "notanemail",
    "@example.com",
    "user@",
    "user@example",
    "user@example.",
    "user name@example.com",
])
def test_api_auto_save_invalid_email(authed_client, app, invalid_email):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    try:
        with patch("app.modules.contacts.controllers.api._get_credentials", return_value="pw"), \
             patch("app.modules.contacts.controllers.api.carddav.discover_address_book") as mock_disc:
            mock_session = MagicMock()
            mock_disc.return_value = (mock_session, "/abook/", "Contacts")

            resp = client.post(
                "/app/contacts/api/auto-save",
                data=json.dumps({"account_id": account_id, "recipients": [{"email": invalid_email, "name": "Bad"}]}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["saved"] == 0
        assert data["skipped"] == 1
    finally:
        os.unlink(paths["cache"])


def test_api_auto_save_mix_valid_and_invalid(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    try:
        with patch("app.modules.contacts.controllers.api._get_credentials", return_value="pw"), \
             patch("app.modules.contacts.controllers.api.carddav.discover_address_book") as mock_disc, \
             patch("app.modules.contacts.controllers.api.carddav.create_contact") as mock_create:
            mock_session = MagicMock()
            mock_disc.return_value = (mock_session, "/abook/", "Contacts")
            mock_create.side_effect = [("/abook/uid-1.vcf", "etag-1"), ("/abook/uid-2.vcf", "etag-2")]

            resp = client.post(
                "/app/contacts/api/auto-save",
                data=json.dumps({
                    "account_id": account_id,
                    "recipients": [
                        {"email": "valid@example.com", "name": "Valid"},
                        {"email": "notanemail", "name": "Bad"},
                        {"email": "also-good@company.org", "name": "Good"},
                        {"email": "@missing.local", "name": "Nope"},
                    ]
                }),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["saved"] == 2
        assert data["skipped"] == 2

        with app.app_context():
            key = get_user_key(user_id)
            conn = open_cache(paths["cache"], key)
            rows = conn.execute("SELECT email_work FROM contacts ORDER BY email_work").fetchall()
            conn.close()
        assert len(rows) == 2
        assert rows[0][0] == "also-good@company.org"
        assert rows[1][0] == "valid@example.com"
    finally:
        os.unlink(paths["cache"])


def test_contact_list_shows_delete_button(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-del-list", "/del.vcf", "e1", "Delete Me", "del@example.com"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.contacts._sync_contacts"):
            resp = client.get("/app/contacts/")
        assert resp.status_code == 200
        assert b"Delete" in resp.data
        assert b"delete-contact-btn" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_create_duplicate_email_rejected(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-existing", "/ex.vcf", "e1", "Existing", "dup@example.com"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.contacts.carddav"):
            resp = client.post("/app/contacts/new", data={
                "fn": "New Person",
                "first_name": "New",
                "last_name": "Person",
                "email_work": "dup@example.com",
            })
        assert resp.status_code == 200
        assert b"already used" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_edit_duplicate_email_rejected(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-a2", "/a2.vcf", "e1", "Alice", "alice@example.com"),
        )
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-b2", "/b2.vcf", "e2", "Bob", "bob@example.com"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.contacts.carddav"):
            resp = client.post(f"/app/contacts/{account_id}/uid-b2/edit", data={
                "fn": "Bob Updated",
                "first_name": "Bob",
                "last_name": "Updated",
                "email_work": "alice@example.com",
            })
        assert resp.status_code == 200
        assert b"already used" in resp.data
    finally:
        os.unlink(paths["cache"])


def test_contact_edit_same_email_allowed(authed_client, app):
    client, user_id, account_id = authed_client
    paths = _setup_test_env(app, account_id)

    from app.modules.contacts.services.cache_db import open_cache
    from app.shared.keys import get_user_key

    with app.app_context():
        key = get_user_key(user_id)
        conn = open_cache(paths["cache"], key)
        conn.execute(
            "INSERT INTO contacts (uid, href, etag, fn, email_work) VALUES (?, ?, ?, ?, ?)",
            ("uid-same", "/same.vcf", "e1", "Same Email", "same@example.com"),
        )
        conn.commit()
        conn.close()

    try:
        with patch("app.modules.contacts.controllers.contacts.carddav") as mock_carddav:
            mock_session = MagicMock()
            mock_carddav.discover_address_book.return_value = (mock_session, "/ab/", "Contacts")
            mock_carddav.update_contact.return_value = "new-etag"
            resp = client.post(f"/app/contacts/{account_id}/uid-same/edit", data={
                "fn": "Same Email Updated",
                "first_name": "Same",
                "last_name": "Email",
                "email_work": "same@example.com",
            })
        assert resp.status_code == 302
    finally:
        os.unlink(paths["cache"])
