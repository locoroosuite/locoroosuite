import json
from unittest.mock import patch, MagicMock

import pytest

from tests.api.conftest import setup_cache_db, cleanup_cache_db, create_api_token, auth_header


@pytest.fixture()
def contacts_api(app, api_customer):
    client, user_id, account_id = api_customer
    with app.app_context():
        token_value, _ = create_api_token(app, user_id)
    cache_path = setup_cache_db(app, account_id)
    yield client, token_value, account_id, cache_path
    cleanup_cache_db(cache_path)


def _seed_contacts_cache(cache_path, dek="a" * 64):
    from app.modules.contacts.services.cache_db import open_cache, upsert_contact
    conn = open_cache(cache_path, dek)
    upsert_contact(
        conn, uid="contact-001", href="/contacts/contact-001.vcf", etag="etag-1",
        vcard_text=(
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:contact-001\r\n"
            "FN:Jane Smith\r\nN:Smith;Jane;;;\r\n"
            "EMAIL;TYPE=work:jane@example.com\r\n"
            "TEL;TYPE=cell:+1-555-0101\r\n"
            "ORG:Acme Corp\r\nTITLE:CEO\r\nNOTE:Important contact\r\n"
            "END:VCARD\r\n"
        ),
    )
    upsert_contact(
        conn, uid="contact-002", href="/contacts/contact-002.vcf", etag="etag-2",
        vcard_text=(
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:contact-002\r\n"
            "FN:Bob Jones\r\nN:Jones;Bob;;;\r\n"
            "EMAIL;TYPE=work:bob@example.com\r\n"
            "END:VCARD\r\n"
        ),
    )
    conn.close()


class TestListContacts:
    def test_empty_list(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.get("/api/v1/contacts", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["data"] == []

    def test_returns_seeded_contacts(self, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)
        resp = client.get("/api/v1/contacts", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 2
        names = {c["fn"] for c in data["data"]}
        assert "Jane Smith" in names
        assert "Bob Jones" in names

    def test_contact_has_expected_fields(self, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)
        resp = client.get("/api/v1/contacts", headers=auth_header(token))
        data = json.loads(resp.data)
        contact = data["data"][0]
        for key in ("id", "uid", "fn", "email_work", "email_home", "phone_work", "phone_cell", "organization", "title", "note"):
            assert key in contact, f"Missing field: {key}"


class TestGetContact:
    def test_not_found(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.get("/api/v1/contacts/99999", headers=auth_header(token))
        assert resp.status_code == 404

    def test_returns_contact_detail(self, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)
        list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
        contacts = json.loads(list_resp.data)["data"]
        jane = next(c for c in contacts if c["fn"] == "Jane Smith")
        contact_id = jane["id"]

        resp = client.get(f"/api/v1/contacts/{contact_id}", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["fn"] == "Jane Smith"
        assert data["email_work"] == "jane@example.com"
        assert "vcard_raw" in data


class TestSearchContacts:
    def test_missing_query_returns_422(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.get("/api/v1/contacts/search", headers=auth_header(token))
        assert resp.status_code == 422

    def test_search_returns_matching(self, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)
        resp = client.get("/api/v1/contacts/search?q=Jane", headers=auth_header(token))
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["data"]) == 1
        assert data["data"][0]["name"] == "Jane Smith"

    def test_search_no_results(self, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)
        resp = client.get("/api/v1/contacts/search?q=nonexistent", headers=auth_header(token))
        assert resp.status_code == 200
        assert json.loads(resp.data)["data"] == []


class TestDeleteContact:
    def test_not_found(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.delete("/api/v1/contacts/99999", headers=auth_header(token))
        assert resp.status_code == 404

    def test_deletes_contact(self, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)
        list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
        contact_id = json.loads(list_resp.data)["data"][0]["id"]

        resp = client.delete(f"/api/v1/contacts/{contact_id}", headers=auth_header(token))
        assert resp.status_code == 204

        list_resp2 = client.get("/api/v1/contacts", headers=auth_header(token))
        assert len(json.loads(list_resp2.data)["data"]) == 1


class TestBulkDeleteContacts:
    def test_empty_items_returns_400(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.post(
            "/api/v1/contacts/bulk/delete",
            json={"items": []},
            headers=auth_header(token),
        )
        assert resp.status_code == 400

    def test_bulk_delete_happy_path(self, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)
        list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
        contacts = json.loads(list_resp.data)["data"]
        contact_ids = [c["id"] for c in contacts]

        resp = client.post(
            "/api/v1/contacts/bulk/delete",
            json={"items": [{"contact_id": contact_ids[0]}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["succeeded"]) == 1
        assert len(data["failed"]) == 0

    def test_bulk_delete_not_found(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.post(
            "/api/v1/contacts/bulk/delete",
            json={"items": [{"contact_id": 99999}]},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["error"]["code"] == "NOT_FOUND"


class TestCreateContact:
    @patch("app.api.controllers.contacts._get_carddav_session")
    def test_create_success(self, mock_session, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        mock_s = MagicMock()
        mock_session.return_value = (mock_s, "http://localhost:5232/user/contacts/", "pass")

        with patch("app.modules.contacts.services.carddav.create_contact") as mock_create:
            mock_create.return_value = (
                "http://localhost:5232/user/contacts/new-uid.vcf",
                '"etag-new"',
            )
            resp = client.post(
                "/api/v1/contacts",
                json={"fn": "MCP Test Contact", "email_work": "mcp-test@example.local"},
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        assert data["fn"] == "MCP Test Contact"
        assert data["uid"]

    def test_create_missing_fn_and_email(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.post(
            "/api/v1/contacts",
            json={},
            headers=auth_header(token),
        )
        assert resp.status_code == 400


class TestUpdateContact:
    @patch("app.api.controllers.contacts._get_carddav_session")
    def test_update_with_relative_href_succeeds(self, mock_session, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)

        list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
        jane_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_s = MagicMock()
        mock_session.return_value = (mock_s, "http://localhost:5232/user/contacts/", "pass")

        with patch("app.modules.contacts.services.carddav.update_contact") as mock_update:
            mock_update.return_value = '"etag-updated"'
            resp = client.put(
                f"/api/v1/contacts/{jane_id}",
                json={"fn": "Jane Smith", "email_work": "jane@example.com", "title": "QA Updated", "organization": "MCP Test Org"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "uid" in data

        get_resp = client.get(f"/api/v1/contacts/{jane_id}", headers=auth_header(token))
        assert get_resp.status_code == 200
        contact = json.loads(get_resp.data)["data"]
        assert contact["title"] == "QA Updated"
        assert contact["organization"] == "MCP Test Org"

    @patch("app.api.controllers.contacts._get_carddav_session")
    def test_update_uses_full_href_when_available(self, mock_session, app, contacts_api):
        client, token, account_id, cache_path = contacts_api

        from app.modules.contacts.services.cache_db import open_cache, upsert_contact
        dek = "a" * 64
        conn = open_cache(cache_path, dek)
        upsert_contact(
            conn, uid="full-href-contact", href="http://localhost:5232/user/contacts/full-href.vcf",
            etag="etag-1",
            vcard_text=(
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:full-href-contact\r\n"
                "FN:Full Href Contact\r\nN:Contact;Full;;;\r\n"
                "EMAIL;TYPE=work:full@example.com\r\nEND:VCARD\r\n"
            ),
        )
        conn.close()

        list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
        contact_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_s = MagicMock()
        mock_session.return_value = (mock_s, "http://localhost:5232/user/contacts/", "pass")

        with patch("app.modules.contacts.services.carddav.update_contact") as mock_update:
            mock_update.return_value = '"etag-updated"'
            resp = client.put(
                f"/api/v1/contacts/{contact_id}",
                json={"fn": "Updated Name"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0]
        assert call_args[1].startswith("http://")

    def test_update_not_found(self, app, contacts_api):
        client, token, account_id, _ = contacts_api
        resp = client.put(
            "/api/v1/contacts/99999",
            json={"fn": "Ghost"},
            headers=auth_header(token),
        )
        assert resp.status_code == 404


class TestCreateContactSchema:
    @patch("app.api.controllers.contacts._get_carddav_session")
    def test_create_returns_full_object(self, mock_session, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        mock_s = MagicMock()
        mock_session.return_value = (mock_s, "http://localhost:5232/user/contacts/", "pass")

        with patch("app.modules.contacts.services.carddav.create_contact") as mock_create:
            mock_create.return_value = (
                "http://localhost:5232/user/contacts/new-uid.vcf",
                '"etag-new"',
            )
            resp = client.post(
                "/api/v1/contacts",
                json={"fn": "Schema Test", "email_work": "schema@example.com"},
                headers=auth_header(token),
            )
        assert resp.status_code == 201
        data = json.loads(resp.data)["data"]
        for key in ("id", "uid", "fn", "email_work", "email_home", "phone_work", "phone_cell", "phone_home", "organization", "title", "note"):
            assert key in data, f"Missing field: {key}"
        assert data["fn"] == "Schema Test"
        assert data["email_work"] == "schema@example.com"


class TestUpdateContactSchema:
    @patch("app.api.controllers.contacts._get_carddav_session")
    def test_update_returns_full_object(self, mock_session, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)

        list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
        jane_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_s = MagicMock()
        mock_session.return_value = (mock_s, "http://localhost:5232/user/contacts/", "pass")

        with patch("app.modules.contacts.services.carddav.get_contact") as mock_get, \
             patch("app.modules.contacts.services.carddav.update_contact") as mock_update:
            mock_get.return_value = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:contact-001\r\n"
                "FN:Jane Smith\r\nN:Smith;Jane;;;\r\n"
                "EMAIL;TYPE=work:jane@example.com\r\n"
                "ORG:Acme Corp\r\nTITLE:CEO\r\nEND:VCARD\r\n"
            )
            mock_update.return_value = '"etag-updated"'
            resp = client.put(
                f"/api/v1/contacts/{jane_id}",
                json={"fn": "Jane Smith", "email_work": "jane@example.com", "organization": "Acme Corp", "title": "CTO"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        for key in ("id", "uid", "fn", "email_work", "email_home", "phone_work", "phone_cell", "phone_home", "organization", "title", "note"):
            assert key in data, f"Missing field: {key}"
        assert data["fn"] == "Jane Smith"
        assert data["title"] == "CTO"


class TestPartialUpdateContact:
    @patch("app.api.controllers.contacts._get_carddav_session")
    def test_partial_update_preserves_omitted_values(self, mock_session, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        _seed_contacts_cache(cache_path)

        list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
        jane_id = json.loads(list_resp.data)["data"][0]["id"]

        mock_s = MagicMock()
        mock_session.return_value = (mock_s, "http://localhost:5232/user/contacts/", "pass")

        with patch("app.modules.contacts.services.carddav.get_contact") as mock_get, \
             patch("app.modules.contacts.services.carddav.update_contact") as mock_update:
            mock_get.return_value = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:contact-001\r\n"
                "FN:Jane Smith\r\nN:Smith;Jane;;;\r\n"
                "EMAIL;TYPE=work:jane@example.com\r\n"
                "ORG:Acme Corp\r\nTITLE:CEO\r\nEND:VCARD\r\n"
            )
            mock_update.return_value = '"etag-updated"'
            resp = client.put(
                f"/api/v1/contacts/{jane_id}",
                json={"organization": "NewCo"},
                headers=auth_header(token),
            )
        assert resp.status_code == 200

        get_resp = client.get(f"/api/v1/contacts/{jane_id}", headers=auth_header(token))
        assert get_resp.status_code == 200
        contact = json.loads(get_resp.data)["data"]
        assert contact["fn"] == "Jane Smith"
        assert contact["email_work"] == "jane@example.com"
        assert contact["organization"] == "NewCo"


class TestCreateUpdateGetDeleteRegression:
    @patch("app.api.controllers.contacts._get_carddav_session")
    def test_full_crud_cycle(self, mock_session, app, contacts_api):
        client, token, account_id, cache_path = contacts_api
        mock_s = MagicMock()
        abook_url = "http://localhost:5232/test%40test.localhost/contacts/"
        mock_session.return_value = (mock_s, abook_url, "pass")

        with patch("app.modules.contacts.services.carddav.create_contact") as mock_create, \
             patch("app.modules.contacts.services.carddav.update_contact") as mock_update, \
             patch("app.modules.contacts.services.carddav.delete_contact") as mock_delete:

            mock_create.return_value = (
                "http://localhost:5232/test%40test.localhost/contacts/9bb663f7.vcf",
                '"etag-create"',
            )
            resp = client.post(
                "/api/v1/contacts",
                json={"fn": "MCP Test Contact", "email_work": "mcp-test@example.local"},
                headers=auth_header(token),
            )
            assert resp.status_code == 201
            json.loads(resp.data)["data"]["uid"]

            list_resp = client.get("/api/v1/contacts", headers=auth_header(token))
            contacts = json.loads(list_resp.data)["data"]
            assert len(contacts) == 1
            contact_id = contacts[0]["id"]

            mock_update.return_value = '"etag-updated"'
            resp = client.put(
                f"/api/v1/contacts/{contact_id}",
                json={
                    "fn": "MCP Test Contact",
                    "email_work": "mcp-test@example.local",
                    "title": "QA Updated",
                    "organization": "MCP Test Org",
                },
                headers=auth_header(token),
            )
            assert resp.status_code == 200

            get_resp = client.get(f"/api/v1/contacts/{contact_id}", headers=auth_header(token))
            assert get_resp.status_code == 200
            contact = json.loads(get_resp.data)["data"]
            assert contact["title"] == "QA Updated"
            assert contact["organization"] == "MCP Test Org"
            assert contact["fn"] == "MCP Test Contact"
            assert contact["email_work"] == "mcp-test@example.local"

            mock_delete.return_value = True
            del_resp = client.delete(
                f"/api/v1/contacts/{contact_id}",
                headers=auth_header(token),
            )
            assert del_resp.status_code == 204

            list_resp2 = client.get("/api/v1/contacts", headers=auth_header(token))
            assert json.loads(list_resp2.data)["data"] == []
