from __future__ import annotations

import logging

from flask import flash, redirect, request, session, url_for

from app.admin import admin_bp
from app.shared import totp as totp_mod
from app.shared.audit import log_audit
from app.shared.auth import require_role
from app.shared.db import db
from app.shared.models.core import User

logger = logging.getLogger(__name__)


@admin_bp.route("/customers/<int:customer_id>/disable-2fa", methods=["POST"])
@require_role("admin")
def disable_customer_2fa(customer_id):
    customer = db.get_or_404(User, customer_id)
    if not totp_mod.is_2fa_enabled(customer):
        flash(f"2FA is not enabled for {customer.email}.", "error")
        return redirect(url_for("admin.customers"))
    totp_mod.disable_2fa(customer)
    logger.info("2FA disabled by admin for customer user_id=%s", customer.id)
    log_audit(
        session.get("user_id"),
        "admin",
        "customer_2fa_disable",
        f"customer={customer.email},totp=disabled",
        request.remote_addr,
        request.headers.get("User-Agent"),
    )
    flash(f"2FA disabled for {customer.email}. All trusted devices were revoked.", "success")
    return redirect(url_for("admin.customers"))
