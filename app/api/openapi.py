import logging
from typing import Any

from flask import Flask
from flask_openapi3.openapi import OpenAPI as _OpenAPI
from flask_openapi3.blueprint import APIBlueprint as _APIBlueprint
from flask_openapi3.models.tag import Tag

from app.api.schemas.common import ErrorResponse

_logger = logging.getLogger(__name__)

_API_PREFIXES = ("/api/v1/", "/api/docs", "/api/openapi.json")


class _ApiMiddleware:
    def __init__(self, main_app_wsgi, api_app_wsgi):
        self._main = main_app_wsgi
        self._api = api_app_wsgi

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if any(path.startswith(p) or path == p.rstrip("/") for p in _API_PREFIXES):
            environ["SCRIPT_NAME"] = "/api"
            environ["PATH_INFO"] = path[len("/api"):]
            return self._api(environ, start_response)
        return self._main(environ, start_response)

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
    )

    if db_uri.startswith("sqlite://"):
        api_app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"check_same_thread": False},
        }

    if "sqlalchemy" not in api_app.extensions:
        db.init_app(api_app)

    with main_app.app_context():
        shared_engine = db.engine
    db._app_engines[api_app] = {None: shared_engine}

    if not hasattr(api_app, "_api_registered"):
        from app.api.controllers import accounts, tokens, mail, contacts, calendar, docs
        api_app.register_api(accounts.bp)
        api_app.register_api(tokens.bp)
        api_app.register_api(mail.bp)
        api_app.register_api(contacts.bp)
        api_app.register_api(calendar.bp)
        api_app.register_api(docs.bp)
        setattr(api_app, "_api_registered", True)

    @main_app.before_request
    def _propagate_sync_manager():
        sync_mgr = getattr(main_app, "sync_manager", None)
        if sync_mgr is not None:
            setattr(api_app, "sync_manager", sync_mgr)

    main_app.wsgi_app = _ApiMiddleware(main_app.wsgi_app, api_app.wsgi_app)
