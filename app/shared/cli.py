from __future__ import annotations

import click
from flask import Flask
from flask.cli import AppGroup

from app.shared.models.core import User
from app.shared import totp as totp_mod

twofa_cli = AppGroup("twofa", help="Two-factor authentication management.")


@twofa_cli.command("disable")
@click.argument("email")
def twofa_disable(email: str):
    """Disable 2FA for a user by email address."""
    user = User.query.filter_by(email=email.lower().strip()).first()
    if not user:
        click.echo(f"User not found: {email}")
        raise click.Abort()
    if not totp_mod.is_2fa_enabled(user):
        click.echo(f"2FA is not enabled for {email}")
        return
    totp_mod.disable_2fa(user)
    click.echo(f"2FA disabled for {email}. All trusted devices revoked.")


def register_cli(app: Flask):
    app.cli.add_command(twofa_cli)
