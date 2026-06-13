import tempfile
import os

from app.modules.contacts.services.cache_db import (
    open_cache,
    upsert_contact,
    get_contact,
    get_contact_by_uid,
    list_contacts,
    count_contacts,
    search_contacts,
    search_contacts_api,
    delete_contact_by_uid,
    get_sync_state,
    set_sync_state,
)


def _make_cache():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = f.name
    f.close()
    key = "0" * 64
    conn = open_cache(path, key)
    return conn, path, key


def _cleanup(conn, path):
    conn.close()
    os.unlink(path)


def test_init_schema():
    conn, path, key = _make_cache()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "contacts" in tables
        assert "addressbook_state" in tables
    finally:
        _cleanup(conn, path)


def test_upsert_and_get():
    conn, path, key = _make_cache()
    try:
        vcard = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid1\r\nFN:Alice\r\nEND:VCARD"
        cid = upsert_contact(conn, "uid1", "/a.vcf", "etag1", vcard)
        contact = get_contact(conn, cid)
        assert contact is not None
        assert contact["fn"] == "Alice"
        assert contact["uid"] == "uid1"
    finally:
        _cleanup(conn, path)


def test_upsert_updates_existing():
    conn, path, key = _make_cache()
    try:
        vcard1 = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid1\r\nFN:Alice\r\nEND:VCARD"
        vcard2 = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid1\r\nFN:Alice Updated\r\nEND:VCARD"
        upsert_contact(conn, "uid1", "/a.vcf", "etag1", vcard1)
        upsert_contact(conn, "uid1", "/a.vcf", "etag2", vcard2)
        assert count_contacts(conn) == 1
        c = get_contact_by_uid(conn, "uid1")
        assert c["fn"] == "Alice Updated"
    finally:
        _cleanup(conn, path)


def test_list_contacts_sorted():
    conn, path, key = _make_cache()
    try:
        upsert_contact(conn, "uid-b", "/b.vcf", "e2", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-b\r\nFN:Bob\r\nEND:VCARD")
        upsert_contact(conn, "uid-a", "/a.vcf", "e1", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-a\r\nFN:Alice\r\nEND:VCARD")
        contacts = list_contacts(conn)
        assert len(contacts) == 2
        assert contacts[0]["fn"] == "Alice"
        assert contacts[1]["fn"] == "Bob"
    finally:
        _cleanup(conn, path)


def test_list_contacts_pagination():
    conn, path, key = _make_cache()
    try:
        for i in range(5):
            upsert_contact(conn, f"uid-{i}", f"/{i}.vcf", f"e{i}",
                           f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-{i}\r\nFN:Contact {i:03d}\r\nEND:VCARD")
        page1 = list_contacts(conn, page=1, per_page=2)
        page2 = list_contacts(conn, page=2, per_page=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["fn"] != page2[0]["fn"]
    finally:
        _cleanup(conn, path)


def test_count_contacts():
    conn, path, key = _make_cache()
    try:
        assert count_contacts(conn) == 0
        upsert_contact(conn, "uid1", "/1.vcf", "e1", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid1\r\nFN:A\r\nEND:VCARD")
        assert count_contacts(conn) == 1
    finally:
        _cleanup(conn, path)


def test_delete_contact():
    conn, path, key = _make_cache()
    try:
        upsert_contact(conn, "uid1", "/1.vcf", "e1", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid1\r\nFN:A\r\nEND:VCARD")
        assert count_contacts(conn) == 1
        delete_contact_by_uid(conn, "uid1")
        assert count_contacts(conn) == 0
    finally:
        _cleanup(conn, path)


def test_search_contacts():
    conn, path, key = _make_cache()
    try:
        upsert_contact(conn, "uid-a", "/a.vcf", "e1",
                       "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-a\r\nFN:Alice Smith\r\nEMAIL;TYPE=WORK:alice@example.com\r\nEND:VCARD")
        upsert_contact(conn, "uid-b", "/b.vcf", "e2",
                       "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-b\r\nFN:Bob Jones\r\nEMAIL;TYPE=WORK:bob@example.com\r\nEND:VCARD")
        results = search_contacts(conn, "Alice")
        assert len(results) == 1
        assert results[0]["fn"] == "Alice Smith"
    finally:
        _cleanup(conn, path)


def test_search_contacts_api():
    conn, path, key = _make_cache()
    try:
        upsert_contact(conn, "uid-api", "/api.vcf", "e1",
                       "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-api\r\nFN:Api User\r\nEMAIL;TYPE=WORK:api@example.com\r\nEND:VCARD")
        results = search_contacts_api(conn, "api")
        assert len(results) == 1
        assert results[0]["fn"] == "Api User"
        assert results[0]["emails"][0]["email"] == "api@example.com"
    finally:
        _cleanup(conn, path)


def test_sync_state():
    conn, path, key = _make_cache()
    try:
        assert get_sync_state(conn, "/ab/") is None
        set_sync_state(conn, "/ab/", "token-1")
        state = get_sync_state(conn, "/ab/")
        assert state["sync_token"] == "token-1"
        assert state["last_sync_at"] is not None
        set_sync_state(conn, "/ab/", "token-2")
        state = get_sync_state(conn, "/ab/")
        assert state["sync_token"] == "token-2"
    finally:
        _cleanup(conn, path)


def test_get_contact_not_found():
    conn, path, key = _make_cache()
    try:
        assert get_contact(conn, 999) is None
        assert get_contact_by_uid(conn, "nope") is None
    finally:
        _cleanup(conn, path)
