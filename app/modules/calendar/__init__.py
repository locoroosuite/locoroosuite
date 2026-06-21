from app.modules.calendar.controllers.helpers import calendar_bp


def register(app):
    from app.modules.calendar.controllers import views, events, api, imip_api  # noqa: F401 (side-effect: registers routes)
    app.register_blueprint(calendar_bp, url_prefix="/app")
