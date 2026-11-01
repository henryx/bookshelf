import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from bookshelf.db import get_db
from bookshelf.db.schema import init_db
from bookshelf.models.users import (
    Role,
    authenticate,
    create_user,
    find_by_email,
    set_password,
)

__all__ = ["init_app"]


@click.command("init-db")
@with_appcontext
def init_db_command() -> None:
    """Create missing database tables."""
    init_db(get_db())
    click.echo("Database initialised.")


@click.command("create-admin")
@click.argument("email")
@click.password_option()
@with_appcontext
def create_admin_command(email: str, password: str) -> None:
    """Create an admin user."""
    conn = get_db()
    if find_by_email(conn, email) is not None:
        raise click.ClickException(f"User {email} already exists")
    user = create_user(conn, email, password, role=Role.ADMIN)
    click.echo(f"Admin {user.email} created.")


@click.command("reset-user-password")
@click.argument("email")
@click.option(
    "--admin-email", prompt="Admin email", help="Admin authorising the reset."
)
@with_appcontext
def reset_user_password_command(email: str, admin_email: str) -> None:
    """Set a new password for a user. Requires admin credentials."""
    conn = get_db()
    admin_password = click.prompt("Admin password", hide_input=True)
    admin = authenticate(conn, admin_email, admin_password)
    if admin is None or not admin.is_admin:
        current_app.logger.warning("Password reset refused for admin %s", admin_email)
        raise click.ClickException("Invalid admin credentials")

    user = find_by_email(conn, email)
    if user is None:
        raise click.ClickException(f"User {email} not found")

    password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
    set_password(conn, user, password)
    current_app.logger.info(
        "Password for %s reset by admin %s", user.email, admin.email
    )
    click.echo(f"Password for {user.email} reset.")


def init_app(app: Flask) -> None:
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(reset_user_password_command)
