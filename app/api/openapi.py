import logging
import threading
from typing import Any

from flask import Flask
from flask_openapi3.blueprint import APIBlueprint as _APIBlueprint
from flask_openapi3.models.tag import Tag
from flask_openapi3.openapi import OpenAPI as _OpenAPI

from app.api.schemas.common import ErrorResponse

_logger = logging.getLogger(__name__)

_API_PREFIXES = ("/api/v1/", "/api/docs", "/api/openapi.json")


class _ApiMiddleware:
    """Routes /api/* to the shared api_app on behalf of a specific main app.

    The api_app singleton is shared across every Flask app in the process
    (the main server, and app.mcp.create_asgi_app's embedded backend).
    Each main app dispatches API traffic with its OWN database engine, so
    the engine binding is refreshed on dispatch — otherwise the app created
    last would permanently repoint /api/v1 at its (empty) database and
    poison token auth for every other app (cross-suite AUTH_INVALID).
    """

    def __init__(self, main_app: Flask, api_app: Flask):
        self._main = main_app
        self._api = api_app
        # Capture the WSGI callables NOW: main_app.wsgi_app may be wrapped
        # again later (e.g. the MCP ASGI proxy); re-reading the attribute at
        # request time would redispatch into the outer wrapper forever.
        self._main_wsgi = main_app.wsgi_app
        self._api_wsgi = api_app.wsgi_app
        self._main_engine: Any = None

    def _engine_for_main(self) -> Any:
        if self._main_engine is None:
            from app.shared.db import db

            with self._main.app_context():
                self._main_engine = db.engine
        return self._main_engine

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if any(path.startswith(p) or path == p.rstrip("/") for p in _API_PREFIXES):
            from app.shared.db import db

            db._app_engines[self._api] = {None: self._engine_for_main()}
            environ["SCRIPT_NAME"] = "/api"
            environ["PATH_INFO"] = path[len("/api") :]
            return self._api_wsgi(environ, start_response)
        return self._main_wsgi(environ, start_response)


_info: Any = {
    "title": "LocoRoomail API",
    "version": "1.0.0",
    "description": "REST API for LocoRoomail — mail, contacts, calendar, and docs.",
}

_security_schemes: Any = {
    "bearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT or API key (lr_...)",
        "description": "Bearer token authentication. Use an API key (lr_...) or OAuth 2.1 JWT.",
    },
}

_DEFAULT_RESPONSES: Any = {
    401: ErrorResponse,
    429: ErrorResponse,
    500: ErrorResponse,
}

api_app = _OpenAPI(
    "api",
    info=_info,
    security_schemes=_security_schemes,
    responses=_DEFAULT_RESPONSES,
    doc_prefix="/docs",
    doc_url="/openapi.json",
    validation_error_status=422,
)


def create_api_blueprint(module_name: str, description: str) -> _APIBlueprint:
    return _APIBlueprint(
        module_name,
        __name__,
        url_prefix="/v1",
        abp_tags=[Tag(name=module_name, description=description)],
        abp_responses=_DEFAULT_RESPONSES,
    )


def register_api_app(main_app: Flask) -> None:
    from app.shared.db import db

    db_uri = main_app.config["SQLALCHEMY_DATABASE_URI"]

    api_app.config.update(
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=main_app.config["SECRET_KEY"],
        TESTING=main_app.config.get("TESTING", False),
        APP_URL=main_app.config.get("APP_URL", ""),
        DOCS_DIR=main_app.config.get("DOCS_DIR", "data/docs"),
        APP_ENV=main_app.config.get("APP_ENV", "development"),
        # Propagated so API-level settings actually apply on the api_app
        # (it is a separate Flask instance with its own config).
        API_RATE_LIMIT_PER_MINUTE=main_app.config.get("API_RATE_LIMIT_PER_MINUTE", 60),
    )

    if db_uri.startswith("sqlite://"):
        api_app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"check_same_thread": False},
        }

    if "sqlalchemy" not in api_app.extensions:
        db.init_app(api_app)

    with main_app.app_context():
        shared_engine = db.engine
    # Initial binding for the first main app; _ApiMiddleware refreshes it
    # per dispatch so every main app serves /api/v1 from its own engine.
    if api_app not in db._app_engines:
        db._app_engines[api_app] = {None: shared_engine}

    if not hasattr(api_app, "_api_registered"):
        from app.api.controllers import (
            accounts,
            calendar,
            chat,
            contacts,
            docs,
            events,
            mail,
            tokens,
        )

        api_app.register_api(accounts.bp)
        api_app.register_api(tokens.bp)
        api_app.register_api(mail.bp)
        api_app.register_api(contacts.bp)
        api_app.register_api(calendar.bp)
        api_app.register_api(chat.bp)
        api_app.register_api(docs.bp)
        api_app.register_api(events.bp)
        # OpenAPI declares neither attribute; assign through Any for the type checker.
        api_app_ext: Any = api_app
        api_app_ext._api_registered = True

    _register_ai_activity_hook(api_app)

    @main_app.before_request
    def _propagate_sync_manager():
        sync_mgr = getattr(main_app, "sync_manager", None)
        if sync_mgr is not None:
            api_app_ext: Any = api_app
            api_app_ext.sync_manager = sync_mgr

    main_app.wsgi_app = _ApiMiddleware(main_app, api_app)


# --- U27: AI activity emission for REST API traffic -------------------------
#
# Every authenticated /api/v1/* request emits an ai_activity SSE event so a
# user with the webmail open sees agent/AI work in real time (U27.1-U27.3).
# Byte-stream endpoints are excluded; identical consecutive events within
# 2s coalesce (first emits immediately, suppressed repeats flush as one
# merged event with the accumulated count when the window lapses — last-wins
# per U27.3, never invisible); emission is fire-and-forget and never fails
# the request.

_BYTESTREAM_SUFFIXES = ("/raw", "/download", "/download/pdf", "/eml")
_BYTESTREAM_SEGMENTS = ("/attachments/",)

_TOOL_RULES: list[tuple[str, str, str]] = [
    # (method, path template, tool name) — segments with {x} match any single segment
    ("POST", "/v1/mail/messages", "mail_send"),
    ("POST", "/v1/mail/drafts", "mail_save_draft"),
    ("DELETE", "/v1/mail/drafts/{uid}", "mail_delete_draft"),
    ("GET", "/v1/mail/folders/{folder}/messages", "mail_list_messages"),
    ("GET", "/v1/mail/messages/{id}", "mail_get_message"),
    ("GET", "/v1/mail/messages/{id}/raw", "mail_get_raw_message"),
    ("GET", "/v1/mail/messages/{id}/attachments/{idx}", "mail_get_attachment"),
    ("GET", "/v1/mail/messages/{id}/attachments/{idx}/view", "mail_view_attachment"),
    ("GET", "/v1/mail/threads/{id}", "mail_get_thread"),
    ("GET", "/v1/mail/folders", "mail_list_folders"),
    ("POST", "/v1/mail/folders", "mail_create_folder"),
    ("POST", "/v1/mail/folders/{folder}/rename", "mail_rename_folder"),
    ("DELETE", "/v1/mail/folders/{folder}", "mail_delete_folder"),
    ("GET", "/v1/mail/search", "mail_search"),
    ("PATCH", "/v1/mail/messages/{id}", "mail_update_flags"),
    ("POST", "/v1/mail/messages/{id}/move", "mail_move_message"),
    ("POST", "/v1/mail/messages/{id}/spam", "mail_report_spam"),
    ("POST", "/v1/mail/messages/{id}/not-spam", "mail_not_spam"),
    ("DELETE", "/v1/mail/messages/{id}", "mail_delete_message"),
    ("POST", "/v1/mail/bulk/move", "mail_bulk_move"),
    ("POST", "/v1/mail/bulk/delete", "mail_bulk_delete"),
    ("POST", "/v1/mail/bulk/flag", "mail_bulk_flag"),
    ("GET", "/v1/calendar/events", "calendar_list_events"),
    ("GET", "/v1/calendar/calendars/{id}/events", "calendar_list_events"),
    ("POST", "/v1/calendar/events", "calendar_create_event"),
    ("PUT", "/v1/calendar/events/{id}", "calendar_update_event"),
    ("DELETE", "/v1/calendar/events/{id}", "calendar_delete_event"),
    ("GET", "/v1/calendar/events/{id}", "calendar_get_event"),
    ("GET", "/v1/calendar/search", "calendar_search_events"),
    ("POST", "/v1/calendar/free-busy", "calendar_check_free_busy"),
    ("GET", "/v1/calendar/calendars", "calendar_list_calendars"),
    ("POST", "/v1/calendar/calendars", "calendar_create_calendar"),
    ("PUT", "/v1/calendar/calendars/{id}", "calendar_update_calendar"),
    ("DELETE", "/v1/calendar/calendars/{id}", "calendar_delete_calendar"),
    ("GET", "/v1/contacts", "contacts_list"),
    ("POST", "/v1/contacts", "contacts_create"),
    ("GET", "/v1/contacts/search", "contacts_search"),
    ("GET", "/v1/contacts/{id}", "contacts_get"),
    ("PUT", "/v1/contacts/{id}", "contacts_update"),
    ("DELETE", "/v1/contacts/{id}", "contacts_delete"),
    ("POST", "/v1/contacts/bulk/delete", "contacts_bulk_delete"),
    ("GET", "/v1/chat/rooms", "chat_list_rooms"),
    ("POST", "/v1/chat/rooms", "chat_create_room"),
    ("GET", "/v1/chat/rooms/{id}/messages", "chat_list_messages"),
    ("POST", "/v1/chat/rooms/{id}/messages", "chat_send_message"),
    ("POST", "/v1/chat/rooms/{id}/join", "chat_join_room"),
    ("POST", "/v1/chat/rooms/{id}/leave", "chat_leave_room"),
    ("POST", "/v1/chat/rooms/{id}/invite", "chat_invite_user"),
    ("POST", "/v1/chat/rooms/{id}/read", "chat_mark_room_read"),
    ("POST", "/v1/chat/messages/{id}/edit", "chat_edit_message"),
    ("POST", "/v1/chat/messages/{id}/react", "chat_react"),
    ("POST", "/v1/chat/messages/{id}/redact", "chat_delete_message"),
    ("GET", "/v1/docs/documents", "docs_list_documents"),
    ("POST", "/v1/docs/documents", "docs_create_document"),
    ("POST", "/v1/docs/documents/upload", "docs_upload_document"),
    ("GET", "/v1/docs/documents/{id}", "docs_get_document"),
    ("PUT", "/v1/docs/documents/{id}", "docs_rename_document"),
    ("DELETE", "/v1/docs/documents/{id}", "docs_delete_document"),
    ("GET", "/v1/docs/documents/{id}/content", "docs_read_content"),
    ("PUT", "/v1/docs/documents/{id}/content", "docs_update_content"),
    ("GET", "/v1/docs/documents/{id}/download", "docs_download_document"),
    ("GET", "/v1/docs/documents/{id}/download/pdf", "docs_export_pdf"),
    ("POST", "/v1/docs/documents/{id}/convert", "docs_convert_document"),
    ("POST", "/v1/docs/documents/{id}/move", "docs_move_document"),
    ("GET", "/v1/docs/documents/{id}/tags", "docs_get_tags"),
    ("PUT", "/v1/docs/documents/{id}/tags", "docs_update_tags"),
    ("GET", "/v1/docs/tags", "docs_list_tags"),
    ("GET", "/v1/docs/documents/{id}/drafts", "docs_list_drafts"),
    ("POST", "/v1/docs/documents/{id}/drafts", "docs_create_draft"),
    ("POST", "/v1/docs/documents/{id}/drafts/{draft}/apply", "docs_apply_draft"),
    ("DELETE", "/v1/docs/documents/{id}/drafts/{draft}", "docs_discard_draft"),
    ("GET", "/v1/docs/folders", "docs_list_folders"),
    ("POST", "/v1/docs/folders", "docs_create_folder"),
    ("POST", "/v1/docs/folders/rename", "docs_rename_folder"),
    ("POST", "/v1/docs/folders/delete", "docs_delete_folder"),
    ("GET", "/v1/accounts", "accounts_list"),
]

_SUMMARY_BODY_KEYS = (
    "to",
    "cc",
    "subject",
    "folder_id",
    "folder",
    "q",
    "calendar_id",
    "summary",
    "name",
)


def _api_tool_name(method: str, path: str) -> str | None:
    for rule_method, template, tool in _TOOL_RULES:
        if rule_method != method:
            continue
        rule_parts = template.strip("/").split("/")
        path_parts = path.strip("/").split("/")
        if len(rule_parts) != len(path_parts):
            continue
        if all(
            rp.startswith("{") or rp == pp for rp, pp in zip(rule_parts, path_parts, strict=False)
        ):
            return tool
    return None


def _is_bytestream(path: str) -> bool:
    if any(path.endswith(suffix) for suffix in _BYTESTREAM_SUFFIXES):
        return True
    return any(segment in path for segment in _BYTESTREAM_SEGMENTS)


def _module_for(path: str) -> str | None:
    parts = path.strip("/").split("/")
    # /v1/<module>/...
    return parts[1] if len(parts) > 2 else None


_coalesce_lock = threading.Lock()
_coalesce_state: dict[tuple, dict[str, Any]] = {}
_COALESCE_WINDOW_S = 2.0


def _flush_coalesced(key: tuple) -> None:
    """Emit the merged (last-wins) event for a burst when its window ends."""

    with _coalesce_lock:
        state = _coalesce_state.get(key)
        if not state or not state.get("suppressed"):
            return
        state["suppressed"] = False
        customer_id, _tool, account_id_str = key
        payload = state["last"]
        count = state["count"]
        _coalesce_state.pop(key, None)
    from app.shared.ui_events import push_ai_activity

    push_ai_activity(
        customer_id,
        payload["tool"],
        payload["summary"],
        account_id=_int_or_none(account_id_str),
        status=payload["status"],
        duration_ms=payload["duration_ms"],
        count=count,
    )


def _api_activity_summary(method: str, path: str, tool: str) -> str:
    from flask import request

    parts: list[str] = []
    for key in _SUMMARY_BODY_KEYS:
        value = request.args.get(key)
        if value:
            parts.append(f"{key}={value[:120]}")
    payload = None
    if method in ("POST", "PATCH", "PUT", "DELETE"):
        payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        for key in _SUMMARY_BODY_KEYS:
            value = payload.get(key)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (list, tuple)):
                value = list(value)[:5]
            elif isinstance(value, str):
                value = value[:120]
            parts.append(f"{key}={value}")
    return tool + (" (" + ", ".join(parts) + ")" if parts else "")


def _request_account_id() -> str:
    """account_id from query params or JSON body (coalesce key + payload)."""
    from flask import request

    value = request.args.get("account_id")
    if not value and request.method in ("POST", "PATCH", "PUT", "DELETE"):
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            value = payload.get("account_id")
    return str(value) if value not in (None, "") else ""


def _register_ai_activity_hook(app: Any) -> None:
    import time

    from flask import g, request

    if getattr(app, "_ai_activity_hook", False):
        return
    app._ai_activity_hook = True

    @app.before_request
    def _mark_ai_activity_start():
        g._ai_activity_started = time.monotonic()

    @app.after_request
    def _emit_ai_activity(response):
        try:
            ctx = getattr(g, "api_context", None)
            if ctx is None or request.path == "/v1/events/publish":
                return response
            if _is_bytestream(request.path):
                return response
            started = getattr(g, "_ai_activity_started", None)
            duration_ms = int((time.monotonic() - started) * 1000) if started is not None else None
            tool = _api_tool_name(request.method, request.path) or (f"{request.method.lower()}_api")
            account_id_str = _request_account_id()
            key = (ctx.get("customer_id"), tool, account_id_str)
            status = "ok" if response.status_code < 400 else f"http_{response.status_code}"
            payload = {
                "tool": tool,
                "summary": _api_activity_summary(request.method, request.path, tool),
                "status": status,
                "duration_ms": duration_ms,
            }
            from app.shared.ui_events import push_ai_activity

            now = time.monotonic()
            with _coalesce_lock:
                state = _coalesce_state.get(key)
                if state and now - state["ts"] < _COALESCE_WINDOW_S:
                    # U27.3: merge into the burst — last-wins data, count
                    # incremented; a timer flushes the merged event when
                    # the window lapses so the action is never invisible.
                    state["count"] += 1
                    state["suppressed"] = True
                    state["last"] = payload
                    state["ts"] = now
                    return response
                _coalesce_state[key] = {
                    "ts": now,
                    "count": 1,
                    "suppressed": False,
                    "last": payload,
                }
            push_ai_activity(
                ctx.get("customer_id"),
                tool,
                payload["summary"],
                account_id=_int_or_none(account_id_str),
                status=status,
                duration_ms=duration_ms,
                count=1,
                module=_module_for(request.path),
            )
            _schedule_flush(key)
        except Exception:
            _logger.warning("ai activity emission failed", exc_info=True)
        return response


def _schedule_flush(key: tuple) -> None:
    import threading

    timer = threading.Timer(_COALESCE_WINDOW_S + 0.2, _flush_coalesced, args=(key,))
    timer.daemon = True
    timer.start()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
