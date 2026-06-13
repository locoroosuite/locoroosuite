from app.modules.mail.controllers.helpers import mail_bp, mail_sse_bp


def register(app):
    from app.modules.mail.controllers import auth, mailbox, message, compose, search, settings, tags, sse, bulk, api_settings
    app.register_blueprint(mail_bp, url_prefix="/app")
    app.register_blueprint(mail_sse_bp, url_prefix="/events")

    @app.context_processor
    def inject_active_send():
        from flask import session
        active_send = session.get("active_send")
        if not active_send:
            return {"active_send": None}
        return {"active_send": active_send}
