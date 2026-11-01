import click
from firebird.driver import Connection
from flask import Flask, current_app
from flask.cli import with_appcontext
from prettytable import PrettyTable

from bookshelf.db import get_db
from bookshelf.db.schema import init_db
from bookshelf.models.users import (
    Role,
    User,
    authenticate,
    create_user,
    find_by_email,
    list_users,
    set_locked,
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


def _require_admin(conn: Connection, admin_email: str, action: str) -> User:
    """
    Prompt for the admin password and return the admin, or abort.
    """
    admin_password = click.prompt("Admin password", hide_input=True)
    admin = authenticate(conn, admin_email, admin_password)
    if admin is None or not admin.is_admin:
        current_app.logger.warning("%s refused for admin %s", action, admin_email)
        raise click.ClickException("Invalid admin credentials")
    return admin


def _require_user(conn: Connection, email: str) -> User:
    user = find_by_email(conn, email)
    if user is None:
        raise click.ClickException(f"User {email} not found")
    return user


admin_email_option = click.option(
    "--admin-email", prompt="Admin email", help="Admin authorising the operation."
)


@click.command("reset-user-password")
@click.argument("email")
@admin_email_option
@with_appcontext
def reset_user_password_command(email: str, admin_email: str) -> None:
    """Set a new password for a user. Requires admin credentials."""
    conn = get_db()
    admin = _require_admin(conn, admin_email, "Password reset")
    user = _require_user(conn, email)

    password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
    set_password(conn, user, password)
    current_app.logger.info(
        "Password for %s reset by admin %s", user.email, admin.email
    )
    click.echo(f"Password for {user.email} reset.")


@click.command("lock-user")
@click.argument("email")
@admin_email_option
@with_appcontext
def lock_user_command(email: str, admin_email: str) -> None:
    """Lock a user so they can no longer log in. Requires admin credentials."""
    conn = get_db()
    admin = _require_admin(conn, admin_email, "Lock")
    user = _require_user(conn, email)
    if user.id == admin.id:
        raise click.ClickException("You cannot lock your own account")

    set_locked(conn, user, locked=True)
    current_app.logger.info("User %s locked by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} locked.")


@click.command("unlock-user")
@click.argument("email")
@admin_email_option
@with_appcontext
def unlock_user_command(email: str, admin_email: str) -> None:
    """Unlock a user. Requires admin credentials."""
    conn = get_db()
    admin = _require_admin(conn, admin_email, "Unlock")
    user = _require_user(conn, email)

    set_locked(conn, user, locked=False)
    current_app.logger.info("User %s unlocked by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} unlocked.")


@click.command("list-users")
@admin_email_option
@with_appcontext
def list_users_command(admin_email: str) -> None:
    """List all users. Requires admin credentials."""
    conn = get_db()
    _require_admin(conn, admin_email, "List users")

    table = PrettyTable(["Email", "Role", "Status"], align="l")
    for user in list_users(conn):
        status = "locked" if user.is_locked else "active"
        table.add_row([user.email, user.role.value, status])
    click.echo(table)


def init_app(app: Flask) -> None:
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(reset_user_password_command)
    app.cli.add_command(lock_user_command)
    app.cli.add_command(unlock_user_command)
    app.cli.add_command(list_users_command)
