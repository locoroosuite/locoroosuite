from app.shared.db import db
from app.shared.models.core import AuditLog


def log_audit(actor_user_id, actor_role, action, details, ip_address, user_agent):
    entry = AuditLog(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=action,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.session.add(entry)
    db.session.commit()
