from app.api.openapi import api_app, register_api_app, create_api_blueprint


def register(app):
    register_api_app(app)
