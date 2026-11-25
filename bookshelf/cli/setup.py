"""The first run: the setup wizard's token, or the first admin without the wizard."""

import click
from flask import current_app
from flask.cli import with_appcontext

from bookshelf.cli.common import min_password_length, prompt_new_password, valid_email
from bookshelf.db import get_session
from bookshelf.models.users import (
    Role,
    UserExistsError,
    admin_exists,
    create_user,
    find_by_email,
)
from bookshelf.setup_token import setup_token

__all__ = ["create_admin_command", "setup_token_command"]


@click.command("setup-token")
@with_appcontext
def setup_token_command() -> None:
    """Show the token the setup wizard asks for."""
    click.echo(setup_token(current_app.config["SECRET_KEY"]))


@click.command("create-admin")
@click.argument("email", callback=valid_email)
@with_appcontext
def create_admin_command(email: str) -> None:
    """Create the first admin user. Refused once any admin exists."""
    session = get_session()
    if admin_exists(session):
        raise click.ClickException("An admin already exists")
    if find_by_email(session, email) is not None:
        raise click.ClickException(f"User {email} already exists")

    password = prompt_new_password("Password")
    try:
        user = create_user(
            session,
            email,
            password,
            role=Role.ADMIN,
            min_password_length=min_password_length(),
        )
    except UserExistsError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Admin {user.email} created.")
