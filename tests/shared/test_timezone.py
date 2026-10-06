"""Tests for shared timezone resolution (U9.1/UX3a, U24.31).

Covers the worker-context path: users with timezone="browser" resolve via
the ``CustomerSettings.browser_tz`` cache (migration 0021) when no Flask
session exists.
"""

from flask import session


class TestResolveUserTimezone:
    def test_explicit_setting_wins_over_cached(self):
        from app.shared.timezone import resolve_user_timezone

        assert (
            resolve_user_timezone("America/New_York", cached_browser_tz="Europe/Madrid")
            == "America/New_York"
        )

    def test_cached_browser_tz_used_without_session(self):
        """Worker context: no session, "browser" setting → persisted cache."""
        from app.shared.timezone import resolve_user_timezone

        assert (
            resolve_user_timezone("browser", cached_browser_tz="Europe/Madrid") == "Europe/Madrid"
        )

    def test_invalid_cached_browser_tz_falls_back_to_utc(self):
        from app.shared.timezone import resolve_user_timezone

        assert resolve_user_timezone("browser", cached_browser_tz="Not/AZone") == "UTC"

    def test_browser_without_cache_falls_back_to_utc(self):
        from app.shared.timezone import resolve_user_timezone

        assert resolve_user_timezone("browser") == "UTC"

    def test_session_browser_tz_wins_over_cached(self, app):
        """Web requests: the session value is fresher than the persisted cache."""
        from app.shared.timezone import resolve_user_timezone

        with app.test_request_context():
            session["_browser_tz"] = "Asia/Tokyo"
            assert (
                resolve_user_timezone("browser", cached_browser_tz="Europe/Madrid") == "Asia/Tokyo"
            )


class TestPersistBrowserTz:
    def _write_settings(self, app, user_id, **kwargs):
        from app.shared.db import db
        from app.shared.models.core import CustomerSettings

        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            if settings is None:
                settings = CustomerSettings()
                settings.customer_id = user_id
                db.session.add(settings)
            for key, value in kwargs.items():
                setattr(settings, key, value)
            db.session.commit()

    def _browser_tz(self, app, user_id):
        from app.shared.db import db
        from app.shared.models.core import CustomerSettings

        with app.app_context():
            db.session.expire_all()
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            return getattr(settings, "browser_tz", None) if settings else None

    def test_browser_tz_persisted_on_customer_request(self, app, authed_client):
        """JS reports the tz via /app/api/set-timezone; the next customer
        request caches it on CustomerSettings for worker-side use."""
        client, user_id, _ = authed_client
        self._write_settings(app, user_id, timezone="browser")
        resp = client.post("/app/api/set-timezone", json={"timezone": "Europe/Madrid"})
        assert resp.status_code == 200
        # The persist hook runs before_request: the set-timezone POST itself
        # still sees the old session, so any subsequent request persists it.
        assert client.get("/app/mail/settings").status_code == 200
        assert self._browser_tz(app, user_id) == "Europe/Madrid"

    def test_explicit_timezone_not_cached(self, app, authed_client):
        """Explicit timezone choice wins everywhere; the cache stays untouched."""
        client, user_id, _ = authed_client
        self._write_settings(app, user_id, timezone="America/New_York")
        resp = client.post("/app/api/set-timezone", json={"timezone": "Europe/Madrid"})
        assert resp.status_code == 200
        assert client.get("/app/mail/settings").status_code == 200
        assert self._browser_tz(app, user_id) is None

    def test_unchanged_value_not_rewritten(self, app, authed_client):
        client, user_id, _ = authed_client
        self._write_settings(app, user_id, timezone="browser", browser_tz="Europe/Madrid")
        resp = client.post("/app/api/set-timezone", json={"timezone": "Europe/Madrid"})
        assert resp.status_code == 200
        assert client.get("/app/mail/settings").status_code == 200
        assert self._browser_tz(app, user_id) == "Europe/Madrid"
