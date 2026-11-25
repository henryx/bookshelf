"""Managing users: each command needs an admin's credentials."""

import click
from flask import current_app
from flask.cli import with_appcontext
from prettytable import PrettyTable

from bookshelf.cli.common import (
    admin_email_option,
    min_password_length,
    prompt_new_password,
    require_admin,
    require_user,
    valid_email,
)
from bookshelf.db import get_session
from bookshelf.models.users import (
    AuthMethod,
    LastAdminError,
    Role,
    UserExistsError,
    create_user,
    delete_user,
    find_by_email,
    list_users,
    set_auth_method,
    set_locked,
    set_password,
    set_role,
)

__all__ = [
    "create_user_command",
    "delete_user_command",
    "demote_user_command",
    "list_users_command",
    "lock_user_command",
    "promote_user_command",
    "reset_user_password_command",
    "set_auth_method_command",
    "unlock_user_command",
]

# local: with a password; google: with their Google account (see google_login).
_AUTH_METHODS = click.Choice(
    [m.value.lower() for m in AuthMethod], case_sensitive=False
)


def _how_they_log_in(auth_method: AuthMethod) -> str:
    return "with Google" if auth_method is AuthMethod.GOOGLE else "with a password"


@click.command("create-user")
@click.argument("email", callback=valid_email)
@click.option(
    "--auth",
    "auth",
    type=_AUTH_METHODS,
    default="local",
    show_default=True,
    help="How the user logs in: local (a password, asked for) or google.",
)
@admin_email_option
@with_appcontext
def create_user_command(email: str, auth: str, admin_email: str) -> None:
    """Create a regular user. Requires admin credentials."""
    session = get_session()
    admin = require_admin(session, admin_email, "Create user")
    if find_by_email(session, email) is not None:
        raise click.ClickException(f"User {email} already exists")

    auth_method = AuthMethod(auth.upper())
    password = (
        prompt_new_password("Password") if auth_method is AuthMethod.LOCAL else None
    )
    try:
        user = create_user(
            session,
            email,
            password,
            auth_method=auth_method,
            min_password_length=min_password_length(),
        )
    except UserExistsError as e:
        raise click.ClickException(str(e)) from e
    current_app.logger.info(
        "User %s created by admin %s, logging in %s",
        user.email,
        admin.email,
        _how_they_log_in(auth_method),
    )
    click.echo(f"User {user.email} created: logs in {_how_they_log_in(auth_method)}.")


@click.command("set-auth-method")
@click.argument("email")
@click.argument("auth", type=_AUTH_METHODS)
@admin_email_option
@with_appcontext
def set_auth_method_command(email: str, auth: str, admin_email: str) -> None:
    """
    Make a user log in locally (a new password, asked for) or with Google.
    Ends their open sessions. Requires admin credentials.
    """
    session = get_session()
    admin = require_admin(session, admin_email, "Set auth method")
    user = require_user(session, email)

    auth_method = AuthMethod(auth.upper())
    password = (
        prompt_new_password("New password") if auth_method is AuthMethod.LOCAL else None
    )
    set_auth_method(
        session,
        user,
        auth_method,
        password=password,
        min_password_length=min_password_length(),
    )
    current_app.logger.info(
        "User %s set to log in %s by admin %s",
        user.email,
        _how_they_log_in(auth_method),
        admin.email,
    )
    click.echo(f"{user.email} now logs in {_how_they_log_in(auth_method)}.")


@click.command("reset-user-password")
@click.argument("email")
@admin_email_option
@with_appcontext
def reset_user_password_command(email: str, admin_email: str) -> None:
    """Set a new password for a user. Requires admin credentials."""
    session = get_session()
    admin = require_admin(session, admin_email, "Password reset")
    user = require_user(session, email)
    if user.auth_method is AuthMethod.GOOGLE:
        raise click.ClickException(
            f"{user.email} logs in with Google and has no password: "
            "set-auth-method makes them local, with a new password"
        )

    password = prompt_new_password("New password")
    set_password(session, user, password, min_password_length=min_password_length())
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
    session = get_session()
    admin = require_admin(session, admin_email, "Promote")
    user = require_user(session, email)
    if user.is_admin:
        click.echo(f"User {user.email} is already an admin.")
        return

    set_role(session, user, Role.ADMIN)
    current_app.logger.info("User %s promoted by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} is now an admin.")


@click.command("demote-user")
@click.argument("email")
@admin_email_option
@with_appcontext
def demote_user_command(email: str, admin_email: str) -> None:
    """Remove the admin role from a user. Requires admin credentials."""
    session = get_session()
    admin = require_admin(session, admin_email, "Demote")
    user = require_user(session, email)
    if user.id == admin.id:
        raise click.ClickException("You cannot demote your own account")
    if not user.is_admin:
        click.echo(f"User {user.email} is not an admin.")
        return

    try:
        set_role(session, user, Role.REGULAR)
    except LastAdminError as e:
        raise click.ClickException(f"{e}: it cannot be demoted") from e
    current_app.logger.info("User %s demoted by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} is now a regular user.")


@click.command("lock-user")
@click.argument("email")
@admin_email_option
@with_appcontext
def lock_user_command(email: str, admin_email: str) -> None:
    """Lock a user so they can no longer log in. Requires admin credentials."""
    session = get_session()
    admin = require_admin(session, admin_email, "Lock")
    user = require_user(session, email)
    if user.id == admin.id:
        raise click.ClickException("You cannot lock your own account")

    set_locked(session, user, locked=True)
    current_app.logger.info("User %s locked by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} locked.")


@click.command("unlock-user")
@click.argument("email")
@admin_email_option
@with_appcontext
def unlock_user_command(email: str, admin_email: str) -> None:
    """Unlock a user. Requires admin credentials."""
    session = get_session()
    admin = require_admin(session, admin_email, "Unlock")
    user = require_user(session, email)

    set_locked(session, user, locked=False)
    current_app.logger.info("User %s unlocked by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} unlocked.")


@click.command("delete-user")
@click.argument("email")
@admin_email_option
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@with_appcontext
def delete_user_command(email: str, admin_email: str, yes: bool) -> None:
    """Permanently delete a user. Requires admin credentials."""
    session = get_session()
    admin = require_admin(session, admin_email, "Delete")
    user = require_user(session, email)
    if user.id == admin.id:
        raise click.ClickException("You cannot delete your own account")
    if not yes:
        click.confirm(f"Delete user {user.email}? This cannot be undone", abort=True)

    try:
        delete_user(session, user)
    except LastAdminError as e:
        raise click.ClickException(f"{e}: it cannot be deleted") from e
    current_app.logger.info("User %s deleted by admin %s", user.email, admin.email)
    click.echo(f"User {user.email} deleted.")


@click.command("list-users")
@admin_email_option
@with_appcontext
def list_users_command(admin_email: str) -> None:
    """List all users. Requires admin credentials."""
    session = get_session()
    require_admin(session, admin_email, "List users")

    table = PrettyTable(["Email", "Role", "Login", "Status", "Last login"], align="l")
    for user in list_users(session):
        status = "locked" if user.is_locked else "active"
        last_login = (
            f"{user.last_login_at:%Y-%m-%d %H:%M} UTC"
            if user.last_login_at
            else "never"
        )
        table.add_row(
            [
                user.email,
                user.role.value,
                user.auth_method.value.lower(),
                status,
                last_login,
            ]
        )
    click.echo(table)
