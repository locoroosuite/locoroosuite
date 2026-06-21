import imaplib
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

E2E_DEFAULT_PASSWORD = "TestPass123!"

APP_URL = "http://localhost:8001"
MAIL_API_URL = "http://localhost:8800"
MAIL_API_KEY = "dev-mail-api-secret"
IMAP_HOST = "localhost"
IMAP_PORT = 143
CARDDAV_URL = "http://localhost:5232"
CALDAV_URL = "http://localhost:5232"

E2E_TEST_USERS = {
    "e2e-test@test.localhost": E2E_DEFAULT_PASSWORD,
    "e2e-test2@test.localhost": E2E_DEFAULT_PASSWORD,
}

_TEST_USERS = {
    **E2E_TEST_USERS,
    "admin@dev.test": E2E_DEFAULT_PASSWORD,
    "manager@test.localhost": E2E_DEFAULT_PASSWORD,
}


def check_services() -> bool:
    try:
        r = requests.get(f"{APP_URL}/app/login", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def wait_for(condition, timeout=10, interval=0.5):
    deadline = time.time() + timeout
    last_exc = None
    while time.time() < deadline:
        try:
            result = condition()
            if result:
                return result
        except Exception as e:
            last_exc = e
        time.sleep(interval)
    if last_exc:
        raise last_exc
    raise TimeoutError(f"Condition not met within {timeout}s")


def login_session(email: str, password: str = None) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{APP_URL}/app/login", data={"email": email, "password": password or _TEST_USERS[email]}, allow_redirects=True)
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code}"
    assert "login" not in r.url or r.url.endswith("/mail/"), f"Login did not redirect to mail: {r.url}"
    return s


def admin_session(email: str = "admin@dev.test", password: str = E2E_DEFAULT_PASSWORD) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{APP_URL}/admin/login", data={"email": email, "password": password}, allow_redirects=True)
    assert r.status_code == 200, f"Admin login failed: {r.status_code}"
    return s


def imap_connect(user: str = None, password: str = None):
    if user is None:
        user = "e2e-test@test.localhost"
    if password is None:
        password = _TEST_USERS.get(user, E2E_DEFAULT_PASSWORD)
    conn = imaplib.IMAP4(host=IMAP_HOST, port=IMAP_PORT)
    conn.login(user, password)
    return conn


def imap_search(user: str, password: str, folder: str, criteria: str = "ALL"):
    conn = imap_connect(user, password)
    try:
        conn.select(folder)
        status, data = conn.search(None, criteria)
        if status != "OK":
            return []
        return [m for m in data[0].split() if m]
    finally:
        conn.logout()


def imap_fetch_subjects(user: str, password: str, folder: str, criteria: str = "ALL"):
    conn = imap_connect(user, password)
    try:
        conn.select(folder)
        status, data = conn.search(None, criteria)
        if status != "OK":
            return []
        subjects = []
        for msg_id in data[0].split():
            status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
            if status == "OK":
                raw = msg_data[0][1].decode("utf-8", errors="replace")
                subject = raw.replace("Subject:", "").strip()
                subjects.append(subject)
        return subjects
    finally:
        conn.logout()


def imap_folder_has_message(user: str, password: str, folder: str, subject_contains: str = None, timeout: int = 10):
    def check():
        conn = imap_connect(user, password)
        try:
            conn.select(folder)
            status, data = conn.search(None, "ALL")
            if status != "OK" or not data[0]:
                return False
            if subject_contains is None:
                return len(data[0].split()) > 0
            for msg_id in data[0].split():
                status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                if status == "OK":
                    raw = msg_data[0][1].decode("utf-8", errors="replace")
                    if subject_contains.lower() in raw.lower():
                        return True
            return False
        finally:
            conn.logout()
    return wait_for(check, timeout=timeout)


def mailapi_get_users(domain: str = "test.localhost") -> list[dict[str, Any]]:
    r = requests.get(
        f"{MAIL_API_URL}/api/users",
        headers={"Authorization": f"Bearer {MAIL_API_KEY}"},
        params={"domain": domain},
        timeout=5,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def mailapi_user_exists(email: str) -> bool:
    domain = email.split("@")[1]
    users = mailapi_get_users(domain)
    return any(u.get("email") == email for u in users)


def mailapi_create_user(email: str, password: str = E2E_DEFAULT_PASSWORD) -> dict:
    r = requests.post(
        f"{MAIL_API_URL}/api/users",
        headers={"Authorization": f"Bearer {MAIL_API_KEY}"},
        json={"email": email, "password": password},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def mailapi_delete_user(email: str) -> bool:
    r = requests.delete(
        f"{MAIL_API_URL}/api/users/{email}",
        headers={"Authorization": f"Bearer {MAIL_API_KEY}"},
        timeout=5,
    )
    return r.status_code in (200, 204, 404)


def cleanup_e2e_users():
    for domain in ("test.localhost", "dev.test", "loco.localhost"):
        users = mailapi_get_users(domain)
        for u in users:
            email = u.get("email", "")
            if email.startswith("e2e-"):
                mailapi_delete_user(email)

    cleaned = False
    try:
        _cleanup_e2e_users_via_docker()
        cleaned = True
    except Exception:
        pass
    if not cleaned:
        try:
            _cleanup_e2e_users_via_db()
            cleaned = True
        except Exception:
            pass


def _cleanup_e2e_users_via_db():
    from app import create_app
    from app.shared.db import db as _db
    from app.shared.models.core import User, CustomerAccount
    app = create_app()
    with app.app_context():
        e2e_users = User.query.filter(User.email.like("e2e-%")).all()
        for user in e2e_users:
            accs = CustomerAccount.query.filter_by(customer_id=user.id).all()
            for acc in accs:
                if acc.cache_db_path:
                    try:
                        import os
                        os.unlink(acc.cache_db_path)
                    except OSError:
                        pass
                _db.session.delete(acc)
            _db.session.delete(user)
        _db.session.commit()


def _cleanup_e2e_users_via_docker():
    import subprocess
    script = (
        "import os, sys\n"
        "sys.path.insert(0, '/app')\n"
        "from app import create_app\n"
        "from app.shared.db import db\n"
        "from app.shared.models.core import User, CustomerAccount\n"
        "app = create_app()\n"
        "with app.app_context():\n"
        "    users = User.query.filter(User.email.like('e2e-%')).all()\n"
        "    for u in list(users):\n"
        "        for a in CustomerAccount.query.filter_by(customer_id=u.id).all():\n"
        "            if a.cache_db_path and os.path.exists(a.cache_db_path):\n"
        "                os.unlink(a.cache_db_path)\n"
        "            db.session.delete(a)\n"
        "        db.session.delete(u)\n"
        "    db.session.commit()\n"
        "    print(f'Deleted {len(users)} e2e users from DB')\n"
    )
    subprocess.run(
        ["docker", "compose", "-f", "docker-compose.dev.yml", "exec", "-T", "app",
         "python", "/dev/stdin"],
        input=script,
        capture_output=True, text=True, timeout=30,
    )


def cleanup_e2e_contacts(user: str = "e2e-test@test.localhost", password: str = None):
    if password is None:
        password = _TEST_USERS.get(user, E2E_DEFAULT_PASSWORD)
    abooks = carddav_get_addressbooks(user, password)
    if not abooks:
        return
    abooks[0]["href"]
    hrefs = carddav_report_contacts(user, password)
    for href in hrefs:
        r = requests.delete(
            f"{CARDDAV_URL}{href}",
            auth=(user, password),
            timeout=5,
        )
        if r.status_code not in (200, 204, 404):
            pass


def carddav_get_addressbooks(user: str, password: str = None) -> list[dict]:
    if password is None:
        password = _TEST_USERS.get(user, E2E_DEFAULT_PASSWORD)
    r = requests.request(
        "PROPFIND",
        f"{CARDDAV_URL}/{user}/",
        auth=(user, password),
        headers={"Depth": "1", "Content-Type": "application/xml"},
        data='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>',
        timeout=5,
    )
    if r.status_code not in (200, 207):
        return []
    all_items = _parse_multistatus(r.text)
    return [item for item in all_items if "addressbook" in item.get("resourcetype_types", [])]


def carddav_report_contacts(user: str, password: str = None) -> list[str]:
    if password is None:
        password = _TEST_USERS.get(user, E2E_DEFAULT_PASSWORD)
    abooks = carddav_get_addressbooks(user, password)
    if not abooks:
        return []
    first_abook = abooks[0]["href"]
    r = requests.request(
        "REPORT",
        f"{CARDDAV_URL}{first_abook}",
        auth=(user, password),
        headers={"Depth": "1", "Content-Type": "application/xml"},
        data='<?xml version="1.0"?><c:addressbook-query xmlns:c="urn:ietf:params:xml:ns:carddav"><d:prop xmlns:d="DAV:"><d:getetag/></d:prop></c:addressbook-query>',
        timeout=5,
    )
    if r.status_code not in (200, 207):
        return []
    results = _parse_multistatus(r.text)
    return [item["href"] for item in results]


def caldav_get_calendars(user: str, password: str = None) -> list[dict]:
    if password is None:
        password = _TEST_USERS.get(user, E2E_DEFAULT_PASSWORD)
    r = requests.request(
        "PROPFIND",
        f"{CALDAV_URL}/calendars/{user}/",
        auth=(user, password),
        headers={"Depth": "1", "Content-Type": "application/xml"},
        data='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>',
        timeout=5,
    )
    if r.status_code not in (200, 207):
        return []
    return _parse_multistatus(r.text)


def caldav_get_events(user: str, calendar_href: str, password: str = None) -> list[dict]:
    if password is None:
        password = _TEST_USERS.get(user, E2E_DEFAULT_PASSWORD)
    r = requests.request(
        "REPORT",
        f"{CALDAV_URL}{calendar_href}",
        auth=(user, password),
        headers={"Depth": "1", "Content-Type": "application/xml"},
        data='<?xml version="1.0"?><c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop xmlns:d="DAV:"><d:getetag/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>',
        timeout=5,
    )
    if r.status_code not in (200, 207):
        return []
    return _parse_multistatus(r.text)


def _parse_multistatus(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    ns = {"d": "DAV:"}
    results = []
    for resp in root.findall("d:response", ns):
        href_el = resp.find("d:href", ns)
        if href_el is None:
            continue
        item: dict[str, Any] = {"href": href_el.text}
        for propstat in resp.findall("d:propstat", ns):
            prop = propstat.find("d:prop", ns)
            if prop is not None:
                for child in prop:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    child_types = [
                        ct.tag.split("}")[-1] if "}" in ct.tag else ct.tag
                        for ct in child
                    ]
                    if child_types:
                        item[tag] = child.text or ""
                        item[f"{tag}_types"] = child_types
                    else:
                        item[tag] = child.text or ""
        results.append(item)
    return results


def _extract_domain_id(html, domain_name):
    match = re.search(
        r'data-domain-id="(\d+)"\s+data-domain-name="'
        + re.escape(domain_name)
        + r'"',
        html,
    )
    return match.group(1) if match else None


def setup_e2e_users(app_url: str = APP_URL):
    admin = admin_session()
    r = admin.get(f"{app_url}/admin/customers")
    assert r.status_code == 200, f"Admin customer list failed: {r.status_code}"

    domain_id = _extract_domain_id(r.text, "test.localhost")
    assert domain_id, "test.localhost domain not found in admin"

    for email, password in E2E_TEST_USERS.items():
        username = email.split("@")[0]
        existing = re.search(
            rf'{re.escape(email)}.*?/admin/customers/(\d+)/',
            r.text,
        )
        if existing:
            continue
        resp = admin.post(
            f"{app_url}/admin/customers/new",
            data={
                "username": username,
                "domain_id": domain_id,
                "password": password,
                "create_mode": "password",
            },
            allow_redirects=True,
        )
        assert resp.status_code == 200, f"Failed to create {email}: {resp.status_code}"

    for email, password in E2E_TEST_USERS.items():
        wait_for(lambda: mailapi_user_exists(email), timeout=15)

    for email, password in E2E_TEST_USERS.items():
        login_session(email, password)


def get_account_id(app_url: str, session) -> str:
    r = session.get(f"{app_url}/app/mail/", allow_redirects=True)
    assert r.status_code == 200
    match = re.search(r'/mail/folder/(\d+)/', r.text)
    assert match, "Could not extract account_id from mail page"
    return match.group(1)
