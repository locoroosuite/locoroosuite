from app.modules.mail.controllers.helpers import mail_bp, mail_sse_bp


def register(app):
    from app.modules.mail.controllers import auth, mailbox, message, compose, search, settings, tags, sse, bulk  # noqa: F401 (side-effect: registers routes)
    app.register_blueprint(mail_bp)
    app.register_blueprint(mail_sse_bp, url_prefix="/events")
