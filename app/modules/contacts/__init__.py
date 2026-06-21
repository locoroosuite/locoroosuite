from app.modules.contacts.controllers.helpers import contacts_bp


def register(app):
    from app.modules.contacts.controllers import contacts, api  # noqa: F401 (side-effect: registers routes)
    app.register_blueprint(contacts_bp, url_prefix="/app")
