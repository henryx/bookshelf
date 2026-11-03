import click
from email_validator import EmailNotValidError, validate_email
from firebird.driver import Connection
from flask import Flask, current_app
from flask.cli import with_appcontext
from prettytable import PrettyTable

from bookshelf.db import get_db
from bookshelf.db.schema import init_db
from bookshelf.models.users import (
    Role,
    User,
    UserExistsError,
    admin_exists,
    authenticate,
    create_user,
    delete_user,
    find_by_email,
    list_users,
    prune_login_failures,
    set_locked,
    set_password,
    set_role,
)

__all__ = ["init_app"]


@click.command("init-db")
@with_appcontext
def init_db_command() -> None:
    """Create missing database tables, columns, constraints and indexes."""
    init_db(get_db())
    click.echo("Database initialised.")


@click.command("prune-login-failures")
@with_appcontext
def prune_login_failures_command() -> None:
    """Delete failed logins too old to count towards a block. Safe to run from cron."""
    deleted = prune_login_failures(
        get_db(), window_minutes=current_app.config["LOGIN_BLOCK_MINUTES"]
    )
    current_app.logger.info("Pruned %d expired login failures", deleted)
    click.echo(f"Removed {deleted} expired login failures.")


def _valid_email(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    """Same rules as the login form, so every new account can log in."""
    try:
        validate_email(value, check_deliverability=False)
    except EmailNotValidError as e:
        raise click.BadParameter(str(e)) from e
    return value


@click.command("create-admin")
@click.argument("email", callback=_valid_email)
@with_appcontext
def create_admin_command(email: str) -> None:
    """Create the first admin user. Refused once any admin exists."""
    conn = get_db()
    if admin_exists(conn):
        raise click.ClickException("An admin already exists")
    if find_by_email(conn, email) is not None:
        raise click.ClickException(f"User {email} already exists")

    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    try:
        user = create_user(conn, email, password, role=Role.ADMIN)
    except UserExistsError as e:
        raise click.ClickException(str(e)) from e
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


@click.command("create-user")
@click.argument("email", callback=_valid_email)
@admin_email_option
@with_appcontext
def create_user_command(email: str, admin_email: str) -> None:
    """Create a regular user. Requires admin credentials."""
    conn = get_db()
    admin = _require_admin(conn, admin_email, "Create user")
    if find_by_email(conn, email) is not None:
        raise click.ClickException(f"User {email} already exists")

    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    try:
        user = create_user(conn, email, password)
    except UserExistsError as e:
        raise click.ClickException(str(e)) from e
    current_app.logger.info("User %s created by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} created.")


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


@click.command("promote-user")
@click.argument("email")
@admin_email_option
@with_appcontext
def promote_user_command(email: str, admin_email: str) -> None:
    """Give a user the admin role. Requires admin credentials."""
    conn = get_db()
    admin = _require_admin(conn, admin_email, "Promote")
    user = _require_user(conn, email)
    if user.is_admin:
        click.echo(f"User {user.email} is already an admin.")
        return

    set_role(conn, user, Role.ADMIN)
    current_app.logger.info("User %s promoted by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} is now an admin.")


@click.command("demote-user")
@click.argument("email")
@admin_email_option
@with_appcontext
def demote_user_command(email: str, admin_email: str) -> None:
    """Remove the admin role from a user. Requires admin credentials."""
    conn = get_db()
    admin = _require_admin(conn, admin_email, "Demote")
    user = _require_user(conn, email)
    if user.id == admin.id:
        raise click.ClickException("You cannot demote your own account")
    if not user.is_admin:
        click.echo(f"User {user.email} is not an admin.")
        return

    set_role(conn, user, Role.REGULAR)
    current_app.logger.info("User %s demoted by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} is now a regular user.")


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


@click.command("delete-user")
@click.argument("email")
@admin_email_option
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@with_appcontext
def delete_user_command(email: str, admin_email: str, yes: bool) -> None:
    """Permanently delete a user. Requires admin credentials."""
    conn = get_db()
    admin = _require_admin(conn, admin_email, "Delete")
    user = _require_user(conn, email)
    if user.id == admin.id:
        raise click.ClickException("You cannot delete your own account")
    if not yes:
        click.confirm(f"Delete user {user.email}? This cannot be undone", abort=True)

    delete_user(conn, user)
    current_app.logger.info("User %s deleted by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} deleted.")


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
    app.cli.add_command(prune_login_failures_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(create_user_command)
    app.cli.add_command(reset_user_password_command)
    app.cli.add_command(promote_user_command)
    app.cli.add_command(demote_user_command)
    app.cli.add_command(lock_user_command)
    app.cli.add_command(unlock_user_command)
    app.cli.add_command(delete_user_command)
    app.cli.add_command(list_users_command)
