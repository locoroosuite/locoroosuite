"""Tests for the per-user text scale applied to the layout (HLD UX8c).

The scale is injected as a CSS custom property on ``<html>`` by the app
factory's ``inject_display_prefs`` context processor; the CSS turns it into
the root font-size. Default users and anonymous pages render no override.
"""

from app.shared.db import db as _db
from app.shared.models.core import CustomerSettings


def _html_tag(body: str) -> str:
    # Line 1 is the doctype; line 2 is the <html ...> tag.
    return body.splitlines()[1]


class TestTextScaleLayout:
    def test_default_user_gets_no_scale_override(self, authed_client):
        client, _user_id, _account_id = authed_client
        resp = client.get("/app/mail/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "--lr-text-scale" not in _html_tag(body)

    def test_large_scale_renders_custom_property_on_html(self, authed_client, app):
        client, user_id, _account_id = authed_client
        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            if not settings:
                settings = CustomerSettings()
                settings.customer_id = user_id
                _db.session.add(settings)
            settings.text_scale = "l"
            _db.session.commit()
        resp = client.get("/app/mail/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        html_tag = _html_tag(body)
        assert '<html lang=' in html_tag
        assert 'style="--lr-text-scale: 1.125"' in html_tag

    def test_xl_scale_renders_1_25(self, authed_client, app):
        client, user_id, _account_id = authed_client
        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            if not settings:
                settings = CustomerSettings()
                settings.customer_id = user_id
                _db.session.add(settings)
            settings.text_scale = "xl"
            _db.session.commit()
        resp = client.get("/app/mail/settings")
        body = resp.get_data(as_text=True)
        html_tag = _html_tag(body)
        assert 'style="--lr-text-scale: 1.25"' in html_tag

    def test_anonymous_page_has_no_scale_override(self, client):
        resp = client.get("/app/login", follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "--lr-text-scale" not in _html_tag(body)

    def test_admin_session_keeps_default_scale(self, app, client):
        from app.shared.models.core import User

        with app.app_context():
            user = User()
            user.email = "admin@example.com"
            user.role = "admin"
            user.is_active = True
            user.password_hash = "x"
            _db.session.add(user)
            _db.session.flush()
            user_id = user.id
            settings = CustomerSettings()
            settings.customer_id = user_id
            settings.text_scale = "xl"
            _db.session.add(settings)
            _db.session.commit()
        with client.session_transaction() as sess:
            sess["role"] = "admin"
            sess["user_id"] = user_id
        resp = client.get("/app/login", follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "--lr-text-scale" not in body
