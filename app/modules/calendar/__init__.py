from app.modules.calendar.controllers.helpers import calendar_bp


def register(app):
    from app.modules.calendar.controllers import (  # noqa: F401 (side-effect: registers routes)
        api,
        events,
        events_api,
        imip_api,
        views,
    )

    app.register_blueprint(calendar_bp, url_prefix="/app")
