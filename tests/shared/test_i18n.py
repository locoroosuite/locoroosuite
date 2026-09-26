"""Tests for the i18n layer (HLD U26).

Covers:
- Locale resolution order: user setting > browser_lang cookie >
  Accept-Language (Spanish variants collapse to es_ES) > English (U26.3).
- Admin/manager sessions are pinned to English (U26.5).
- The served JS catalog endpoint and its cache versioning (U26.6).
- ``<html lang>`` reflects the resolved locale.
- The es_ES catalog is compiled and contains spot-check keys.
- The language migration adds the customer_settings column (U26.4).
"""

import sqlite3

from app.shared.app_migrations import APP_DB_MIGRATIONS
from app.shared.migrations import run_migrations, table_columns


class TestLocaleResolution:
    def test_spanish_variants_map_to_es_es(self, app):
        from app.shared.i18n import resolve_locale

        for tag in ("es", "es-ES", "es-MX", "es-AR"):
            with app.test_request_context(
                "/app/login", headers={"Accept-Language": f"{tag},es;q=0.8,en;q=0.5"}
            ):
                assert resolve_locale() == "es_ES", tag

    def test_non_spanish_falls_back_to_english(self, app):
        from app.shared.i18n import resolve_locale

        for tag in ("fr-FR", "de-DE", "en-GB", "pt-BR"):
            with app.test_request_context("/app/login", headers={"Accept-Language": tag}):
                locale = resolve_locale()
                assert locale in (None, "en"), tag

    def test_cookie_wins_over_accept_language(self, app):
        from app.shared.i18n import resolve_locale

        with app.test_request_context(
            "/app/login",
            headers={"Accept-Language": "en-GB,en;q=0.9"},
        ) as ctx:
            ctx.request.cookies = {"browser_lang": "es"}
            assert resolve_locale() == "es_ES"

    def test_setting_wins_over_cookie_and_header(self, app, authed_client):
        from app.shared.db import db
        from app.shared.models.core import CustomerSettings

        client, user_id, _account_id = authed_client
        with app.app_context():
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            if settings is None:
                settings = CustomerSettings()
                settings.customer_id = user_id
                db.session.add(settings)
            settings.language = "en"
            db.session.commit()

        # Spanish browser + Spanish cookie, but the explicit setting (en) wins.
        client.set_cookie("browser_lang", "es")
        resp = client.get(
            "/app/i18n/messages.js",
            headers={"Accept-Language": "es-ES,es;q=0.9"},
        )
        assert resp.status_code == 200
        assert b'locale: "en"' in resp.data

    def test_admin_role_pinned_to_english(self, app):
        from flask import session

        from app.shared.i18n import resolve_locale

        with app.test_request_context("/admin/", headers={"Accept-Language": "es-ES,es;q=0.9"}):
            session["role"] = "admin"
            session["user_id"] = 1
            assert resolve_locale() == "en"

    def test_invalid_cookie_value_ignored(self, app):
        from app.shared.i18n import resolve_locale

        with app.test_request_context(
            "/app/login", headers={"Accept-Language": "en-US,en;q=0.9"}
        ) as ctx:
            ctx.request.cookies = {"browser_lang": "fr"}
            assert resolve_locale() in (None, "en")


class TestMessagesEndpoint:
    def test_spanish_browser_gets_catalog(self, client):
        resp = client.get("/app/i18n/messages.js", headers={"Accept-Language": "es-ES,es;q=0.9"})
        assert resp.status_code == 200
        assert resp.mimetype == "text/javascript"
        body = resp.get_data(as_text=True)
        assert 'locale: "es_ES"' in body
        assert "window.LR_I18N" in body
        assert ".t = function" in body
        assert "messages" in body

    def test_english_browser_gets_empty_catalog(self, client):
        resp = client.get("/app/i18n/messages.js", headers={"Accept-Language": "en-GB"})
        assert resp.status_code == 200
        assert b'locale: "en"' in resp.data
        assert b"messages: {}" in resp.data

    def test_cache_control_set(self, client):
        resp = client.get("/app/i18n/messages.js", headers={"Accept-Language": "es"})
        assert "max-age" in resp.headers.get("Cache-Control", "")


class TestHtmlLang:
    def _client_with_domain(self, app, client):
        from app.shared.db import db
        from app.shared.models.core import Domain

        with app.app_context():
            if Domain.query.first() is None:
                domain = Domain()
                domain.name = "example.com"
                domain.is_active = True
                domain.status = "active"
                domain.imap_host = "imap.example.com"
                domain.imap_port = 993
                domain.imap_tls = True
                domain.smtp_host = "smtp.example.com"
                domain.smtp_port = 587
                domain.smtp_tls_mode = "starttls"
                db.session.add(domain)
                db.session.commit()
        return client

    def test_login_page_lang_attribute(self, app, client):
        client = self._client_with_domain(app, client)
        resp = client.get("/app/login", headers={"Accept-Language": "es-AR,es;q=0.9"})
        assert resp.status_code == 200
        assert b'<html lang="es-ES"' in resp.data

    def test_english_default_lang_attribute(self, app, client):
        client = self._client_with_domain(app, client)
        resp = client.get("/app/login", headers={"Accept-Language": "fr-FR"})
        assert resp.status_code == 200
        assert b'<html lang="en"' in resp.data


class TestEsCatalog:
    def test_catalog_compiled_and_populated(self):
        import os

        mo = "app/translations/es_ES/LC_MESSAGES/messages.mo"
        assert os.path.exists(mo), "compiled es_ES catalog is missing"
        assert os.path.getsize(mo) > 1000

    def test_spot_check_translations(self):
        from babel.support import Translations

        t = Translations.load("app/translations", ["es_ES"])
        # mail / compose / settings core vocabulary
        for msgid, expected in (
            ("Recibidos", None),  # sanity: keys below must exist
            ("Log out", "Cerrar sesión"),
            ("Draft", "Borrador"),
        ):
            got = t.gettext(msgid)
            if expected is None:
                continue
            assert got == expected, f"{msgid!r} -> {got!r}"

    def test_no_untranslated_entries(self):
        """Every msgid in the es_ES .po must have a translation (U26.1).

        Multiline-aware: a long msgstr is written as ``msgstr ""`` followed by
        continuation lines, so a naive line scan would false-positive.
        """
        import re

        po = "app/translations/es_ES/LC_MESSAGES/messages.po"
        with open(po, encoding="utf-8") as fh:
            text = fh.read()
        untranslated = 0
        for block in re.split(r"\n\n", text):
            if block.startswith(("#~", "#, obsolete")):
                continue
            mid = re.search(r"msgid ((?:\"(?:[^\"\\]|\\.)*\"\n?)+)", block)
            mstr = re.search(r"msgstr ((?:\"(?:[^\"\\]|\\.)*\"\n?)+)", block)
            if not mid or not mstr:
                continue
            msgid = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', mid.group(1)))
            msgstr = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', mstr.group(1)))
            if msgid and not msgstr:
                untranslated += 1
        assert untranslated == 0, f"{untranslated} untranslated entries in es_ES catalog"


class TestWorkerTranslation:
    """U26 follow-up: worker threads (push, send worker) translate in the
    user's locale without a request context."""

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

    def test_locale_for_user_explicit_setting(self, app, authed_client):
        from app.shared.i18n import locale_for_user

        _client, user_id, _ = authed_client
        self._write_settings(app, user_id, language="es")
        with app.app_context():
            assert locale_for_user(user_id) == "es_ES"

    def test_locale_for_user_browser_cache(self, app, authed_client):
        from app.shared.i18n import locale_for_user

        _client, user_id, _ = authed_client
        self._write_settings(app, user_id, language="browser", browser_locale="es_ES")
        with app.app_context():
            assert locale_for_user(user_id) == "es_ES"

    def test_locale_for_user_missing_settings_row(self, app, authed_client):
        from app.shared.i18n import locale_for_user

        _client, user_id, _ = authed_client
        with app.app_context():
            assert locale_for_user(user_id) == "en"

    def test_translate_for_user_spanish(self, app, authed_client):
        from app.shared.i18n import translate_for_user

        _client, user_id, _ = authed_client
        self._write_settings(app, user_id, language="es")
        with app.app_context():
            assert translate_for_user(user_id, "Log out") == "Cerrar sesión"
            assert (
                translate_for_user(user_id, "%(count)d new messages in your inbox", count=3)
                == "Tienes 3 mensajes nuevos en Recibidos"
            )

    def test_translate_for_user_english(self, app, authed_client):
        from app.shared.i18n import translate_for_user

        _client, user_id, _ = authed_client
        self._write_settings(app, user_id, language="en")
        with app.app_context():
            assert translate_for_user(user_id, "Log out") == "Log out"

    def test_browser_locale_persisted_on_request(self, app, authed_client):
        """A Spanish-browser customer with language=auto gets the locale cached
        on CustomerSettings for worker-side use."""
        from app.shared.db import db
        from app.shared.models.core import CustomerSettings

        client, user_id, _ = authed_client
        self._write_settings(app, user_id, language="browser")
        client.set_cookie("browser_lang", "es")
        resp = client.get("/app/mail/settings")
        assert resp.status_code == 200
        with app.app_context():
            db.session.expire_all()
            settings = CustomerSettings.query.filter_by(customer_id=user_id).first()
            assert settings is not None
            assert settings.browser_locale == "es_ES"

    def test_send_error_message_translated_for_spanish_user(self, app, authed_client):
        from app.modules.mail.controllers.helpers import _send_error_message

        _client, user_id, _ = authed_client
        self._write_settings(app, user_id, language="es")
        with app.app_context():
            got = _send_error_message(Exception("auth failed"), user_id=user_id)
            assert got == (
                "Error de autenticación en el servidor de correo. "
                "Reinténtalo o verifica las credenciales de la cuenta."
            )
            # English msgid when no user context is available
            assert _send_error_message(Exception("auth failed")) == (
                "Mail server authentication failed. Retry or verify account credentials."
            )

    def test_send_error_msgids_are_in_catalog(self):
        """The N_-marked send-error strings must stay in the es catalog."""
        from babel.support import Translations

        t = Translations.load("app/translations", ["es_ES"])
        assert t.gettext(
            "Daily sending limit reached. Contact support to increase your quota."
        ) == (
            "Has alcanzado el límite de envío diario. Contacta con soporte para ampliar tu cuota."
        )
        assert t.gettext("Message sent, but saving to Sent failed.") == (
            "Mensaje enviado, pero no se pudo guardar en Enviados."
        )


class TestLanguageMigration:
    def test_language_column_added_to_legacy_schema(self):
        """Build the pre-migration schema and verify 0016 adds the column."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE customer_settings (
                customer_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(id),
                polling_interval INTEGER NOT NULL DEFAULT 60,
                preview_pane_default BOOLEAN NOT NULL DEFAULT 0,
                sort_order VARCHAR(32) NOT NULL DEFAULT 'date_desc',
                timezone VARCHAR(64) NOT NULL DEFAULT 'browser'
            )
            """
        )
        conn.commit()

        # The full chain is safe here: every migration self-guards, and the
        # runner creates/uses its own _schema_migrations tracking table.
        run_migrations(conn, APP_DB_MIGRATIONS)

        cols = table_columns(conn, "customer_settings")
        conn.close()
        assert "language" in cols
        assert "browser_locale" in cols
