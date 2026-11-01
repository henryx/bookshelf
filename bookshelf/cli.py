import click
from flask import Flask
from flask.cli import with_appcontext

from bookshelf.db import get_db
from bookshelf.db.schema import init_db
from bookshelf.models.users import Role, create_user, find_by_email

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


def init_app(app: Flask) -> None:
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
