from app.modules.docs.controllers.helpers import docs_bp


def register(app):
    from app.modules.docs.controllers import docs, wopi, sharing  # noqa: F401 (side-effect: registers routes)
    app.register_blueprint(docs_bp, url_prefix="/app")
