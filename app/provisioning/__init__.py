from __future__ import annotations

from flask import Flask

from app.provisioning.controllers import provision_bp


def register(app: Flask) -> None:
    app.register_blueprint(provision_bp)
