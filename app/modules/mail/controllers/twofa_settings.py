from __future__ import annotations

import logging

from flask import session, request, redirect, url_for, render_template, Response

from app.shared.db import db
from app.shared.models.core import User
from app.shared.auth import require_customer
from app.shared import totp as totp_mod
from app.modules.mail.controllers.helpers import mail_bp

logger = logging.getLogger(__name__)


def _current_user() -> User | None:
    return db.session.get(User, session.get("user_id"))


@mail_bp.route("/mail/settings/security")
@require_customer
def twofa_settings():
    user = _current_user()
    if not user:
        return redirect(url_for("mail.login"))
    enabled = totp_mod.is_2fa_enabled(user)
    backup_remaining = totp_mod.backup_codes_remaining(user) if enabled else 0
    devices = totp_mod.list_trusted_devices(user.id) if enabled else []
    return render_template(
        "twofa_settings.html",
        twofa_enabled=enabled,
        backup_remaining=backup_remaining,
        devices=[_device_info(d) for d in devices],
        pending_secret=session.pop("_pending_totp_secret", None),
        backup_codes=session.pop("_new_backup_codes", None),
    )


@mail_bp.route("/mail/settings/security/enable", methods=["POST"])
@require_customer
def twofa_enable():
    user = _current_user()
    if not user:
        return redirect(url_for("mail.login"))
    if totp_mod.is_2fa_enabled(user):
        return redirect(url_for("mail.twofa_settings"))
    secret = totp_mod.generate_secret()
    session["_pending_totp_secret"] = secret
    return redirect(url_for("mail.twofa_confirm"))


@mail_bp.route("/mail/settings/security/confirm", methods=["GET", "POST"])
@require_customer
def twofa_confirm():
    user = _current_user()
    if not user:
        return redirect(url_for("mail.login"))
    secret = session.get("_pending_totp_secret")
    if not secret:
        return redirect(url_for("mail.twofa_settings"))

    if request.method == "GET":
        uri = totp_mod.build_provisioning_uri(secret, user.email)
        return render_template(
            "twofa_confirm.html",
            secret=secret,
            otpauth_uri=uri,
            error=None,
        )

    code = request.form.get("code", "").strip()
    if not totp_mod.verify_code(secret, code):
        uri = totp_mod.build_provisioning_uri(secret, user.email)
        return render_template(
            "twofa_confirm.html",
            secret=secret,
            otpauth_uri=uri,
            error="Invalid code. Please try again.",
        )

    session.pop("_pending_totp_secret", None)
    codes = totp_mod.enable_2fa(user, secret)
    logger.info("2FA enabled for customer user_id=%s", user.id)
    session["_new_backup_codes"] = codes
    return redirect(url_for("mail.twofa_settings"))


@mail_bp.route("/mail/settings/security/qr")
@require_customer
def twofa_qr():
    secret = session.get("_pending_totp_secret")
    if not secret:
        return Response("", status=404)
    user = _current_user()
    email = user.email if user else ""
    uri = totp_mod.build_provisioning_uri(secret, email)
    png = totp_mod.generate_qr_png(uri)
    return Response(png, mimetype="image/png")


@mail_bp.route("/mail/settings/security/disable", methods=["POST"])
@require_customer
def twofa_disable():
    user = _current_user()
    if not user:
        return redirect(url_for("mail.login"))
    code = request.form.get("code", "").strip()
    verified = totp_mod.verify_code(user.totp_secret, code) if user.totp_secret else False
    if not verified and not totp_mod.verify_backup_code(user, code):
        return render_template(
            "twofa_settings.html",
            twofa_enabled=True,
            backup_remaining=totp_mod.backup_codes_remaining(user),
            devices=[_device_info(d) for d in totp_mod.list_trusted_devices(user.id)],
            disable_error="Invalid code. Please try again.",
        )
    totp_mod.disable_2fa(user)
    logger.info("2FA disabled for customer user_id=%s", user.id)
    return redirect(url_for("mail.twofa_settings"))


@mail_bp.route("/mail/settings/security/regenerate-codes", methods=["POST"])
@require_customer
def twofa_regenerate():
    user = _current_user()
    if not user or not totp_mod.is_2fa_enabled(user):
        return redirect(url_for("mail.twofa_settings"))
    code = request.form.get("code", "").strip()
    verified = totp_mod.verify_code(user.totp_secret, code)
    if not verified and not totp_mod.verify_backup_code(user, code):
        return render_template(
            "twofa_settings.html",
            twofa_enabled=True,
            backup_remaining=totp_mod.backup_codes_remaining(user),
            devices=[_device_info(d) for d in totp_mod.list_trusted_devices(user.id)],
            regen_error="Invalid code. Please try again.",
        )
    codes = totp_mod.regenerate_backup_codes(user)
    session["_new_backup_codes"] = codes
    return redirect(url_for("mail.twofa_settings"))


@mail_bp.route("/mail/settings/security/devices/<int:device_id>/revoke", methods=["POST"])
@require_customer
def twofa_revoke_device(device_id):
    user = _current_user()
    if not user:
        return redirect(url_for("mail.login"))
    totp_mod.revoke_trusted_device(device_id, user.id)
    return redirect(url_for("mail.twofa_settings"))


@mail_bp.route("/mail/settings/security/devices/revoke-all", methods=["POST"])
@require_customer
def twofa_revoke_all_devices():
    user = _current_user()
    if not user:
        return redirect(url_for("mail.login"))
    totp_mod.revoke_all_trusted_devices(user.id)
    return redirect(url_for("mail.twofa_settings"))


def _device_info(device):
    return {
        "id": device.id,
        "description": totp_mod.describe_user_agent(device.user_agent),
        "created_at": device.created_at,
        "last_used_at": device.last_used_at,
    }
