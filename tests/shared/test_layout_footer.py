"""Tests for the version footer (HLD N7).

The footer renders on authenticated pages with the version from the root
package.json, and is absent on unauthenticated pages (login).
"""

import json
from pathlib import Path

EXPECTED_VERSION = json.loads(
    Path(__file__).resolve().parent.parent.parent.joinpath("package.json").read_text()
)["version"]


class TestVersionFooter:
    def test_footer_on_authenticated_page(self, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.get("/app/mail/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "<footer" in body
        assert f"LocoRooSuite v{EXPECTED_VERSION}" in body

    def test_no_footer_on_unauthenticated_page(self, client):
        # /app/login redirects to setup when no domains exist; either way the
        # final page is unauthenticated and must not leak the version.
        resp = client.get("/app/login", follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "<footer" not in body
        assert f"v{EXPECTED_VERSION}" not in body
