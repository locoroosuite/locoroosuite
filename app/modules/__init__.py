from app.modules.mail.controllers.helpers import mail_bp, mail_sse_bp


def register(app):
    from app.modules.mail.controllers import (  # noqa: F401 (side-effect: registers routes)
        auth,
        bulk,
        compose,
        mailbox,
        message,
        search,
        settings,
        sse,
        tags,
    )

    app.register_blueprint(mail_bp)
    app.register_blueprint(mail_sse_bp, url_prefix="/events")
