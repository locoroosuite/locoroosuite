"""Tests for the PWA shell routes (U24.12 - U24.15)."""


class TestPwaRoutes:
    def test_manifest(self, client):
        resp = client.get("/manifest.webmanifest")
        assert resp.status_code == 200
        assert resp.mimetype == "application/manifest+json"
        data = resp.get_json()
        assert data["name"] == "LocoRooSuite"
        assert data["display"] == "standalone"
        assert data["start_url"] == "/app/mail/"
        assert data["scope"] == "/"
        assert data["theme_color"] == "#0f172a"
        purposes = {icon["purpose"] for icon in data["icons"]}
        assert purposes == {"any", "maskable"}

    def test_service_worker_served_no_cache(self, client):
        resp = client.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.mimetype
        assert resp.headers["Cache-Control"] == "no-cache"
        body = resp.get_data(as_text=True)
        assert "lr-shell-" in body
        assert "/events/" in body  # SSE paths are never intercepted
        assert "notificationclick" in body

    def test_offline_page_renders(self, client):
        resp = client.get("/offline")
        assert resp.status_code == 200
        assert b"You&#39;re offline" in resp.data or b"You're offline" in resp.data
        assert b"/app/mail/" in resp.data
