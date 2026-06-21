from flask import Blueprint

admin_bp = Blueprint("admin", __name__, template_folder="templates")
manager_bp = Blueprint("manager", __name__, template_folder="templates")
imports_bp = Blueprint("imports", __name__, template_folder="templates")

def register(app):
    from .controllers import admin as admin_ctrl  # noqa: F401 (side-effect: registers routes)
    from .controllers import manager as manager_ctrl  # noqa: F401 (side-effect: registers routes)
    from .controllers import imports as imports_ctrl  # noqa: F401 (side-effect: registers routes)
    from .controllers import auth as auth_ctrl
    from .controllers import twofa_settings as twofa_ctrl  # noqa: F401 (side-effect: registers routes)
    app.register_blueprint(auth_ctrl.auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(manager_bp, url_prefix="/admin/manager")
    app.register_blueprint(imports_bp, url_prefix="/imports")
