from app.modules.chat.controllers.helpers import chat_bp


def register(app):
    from app.modules.chat.controllers import (  # noqa: F401 (side-effect: registers routes)
        api,
        calls,
        stream,
        views,
    )

    app.register_blueprint(chat_bp, url_prefix="/app")
