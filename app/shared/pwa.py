"""PWA shell routes (U24.12 - U24.15): manifest, service worker, offline page.

Registered from the app factory like the module blueprints.
"""

import json
import logging

from flask import Blueprint, Response, current_app, render_template, send_from_directory

_logger = logging.getLogger(__name__)

_bp = Blueprint("pwa", __name__)

_MANIFEST = {
    "name": "LocoRooSuite",
    "short_name": "LocoRooSuite",
    "description": "LocoRooSuite — private mail, contacts, calendar and docs.",
    "start_url": "/app/mail/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f8fafc",
    "theme_color": "#0f172a",
    "icons": [
        {
            "src": "/static/img/icons/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/static/img/icons/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/static/img/icons/icon-maskable-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "maskable",
        },
        {
            "src": "/static/img/icons/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
}


@_bp.route("/manifest.webmanifest")
def manifest() -> Response:
    resp = Response(json.dumps(_MANIFEST), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@_bp.route("/sw.js")
def service_worker() -> Response:
    static_folder = current_app.static_folder
    if static_folder is None:
        raise RuntimeError("PWA service worker requires a static folder; none is configured")
    resp = send_from_directory(
        static_folder, "js/sw.js", mimetype="application/javascript", max_age=0
    )
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@_bp.route("/offline")
def offline() -> str:
    return render_template("offline.html")


def register(app) -> None:
    app.register_blueprint(_bp)
