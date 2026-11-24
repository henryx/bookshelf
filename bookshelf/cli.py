import click
from email_validator import EmailNotValidError, validate_email
from flask import Flask, current_app
from flask.cli import with_appcontext
from prettytable import PrettyTable
from sqlalchemy.orm import Session

from bookshelf.db import get_engine, get_session
from bookshelf.db.schema import init_db
from bookshelf.models.books import CopyMode, copy_collection
from bookshelf.models.users import (
    AuthMethod,
    LastAdminError,
    PasswordTooShortError,
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
    set_auth_method,
    set_locked,
    set_password,
    set_role,
    validate_new_password,
)
from bookshelf.setup_token import setup_token

__all__ = ["init_app"]


@click.command("init-db")
@with_appcontext
def init_db_command() -> None:
    """Create missing database tables, columns, constraints and indexes."""
    init_db(get_engine(), search_language=current_app.config["SEARCH_LANGUAGE"])
    click.echo("Database initialised.")


@click.command("prune-login-failures")
@with_appcontext
def prune_login_failures_command() -> None:
    """Delete failed logins too old to count towards a block. Safe to run from cron."""
    deleted = prune_login_failures(
        get_session(), window_minutes=current_app.config["LOGIN_BLOCK_MINUTES"]
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


def _min_password_length() -> int:
    return current_app.config["MIN_PASSWORD_LENGTH"]


def _prompt_new_password(label: str) -> str:
    """Prompt (with confirmation) until the password is long enough."""
    while True:
        password = click.prompt(label, hide_input=True, confirmation_prompt=True)
        try:
            validate_new_password(password, min_length=_min_password_length())
        except PasswordTooShortError as e:
            click.echo(f"Error: {e}", err=True)
            continue
        return password


@click.command("setup-token")
@with_appcontext
def setup_token_command() -> None:
    """Show the token the setup wizard asks for."""
    click.echo(setup_token(current_app.config["SECRET_KEY"]))


@click.command("create-admin")
@click.argument("email", callback=_valid_email)
@with_appcontext
def create_admin_command(email: str) -> None:
    """Create the first admin user. Refused once any admin exists."""
    session = get_session()
    if admin_exists(session):
        raise click.ClickException("An admin already exists")
    if find_by_email(session, email) is not None:
        raise click.ClickException(f"User {email} already exists")

    password = _prompt_new_password("Password")
    try:
        user = create_user(
            session,
            email,
            password,
            role=Role.ADMIN,
            min_password_length=_min_password_length(),
        )
    except UserExistsError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Admin {user.email} created.")


def _require_admin(session: Session, admin_email: str, action: str) -> User:
    """
    Prompt for the admin password and return the admin, or abort.
    """
    admin_password = click.prompt("Admin password", hide_input=True)
    admin = authenticate(session, admin_email, admin_password)
    if admin is None or not admin.is_admin:
        current_app.logger.warning("%s refused for admin %s", action, admin_email)
        raise click.ClickException("Invalid admin credentials")
    return admin


def _require_user(session: Session, email: str) -> User:
    user = find_by_email(session, email)
    if user is None:
        raise click.ClickException(f"User {email} not found")
    return user


admin_email_option = click.option(
    "--admin-email", prompt="Admin email", help="Admin authorising the operation."
)

# local: with a password; google: with their Google account (see google_login).
_AUTH_METHODS = click.Choice(
    [m.value.lower() for m in AuthMethod], case_sensitive=False
)


def _how_they_log_in(auth_method: AuthMethod) -> str:
    return "with Google" if auth_method is AuthMethod.GOOGLE else "with a password"


@click.command("create-user")
@click.argument("email", callback=_valid_email)
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
    admin = _require_admin(session, admin_email, "Create user")
    if find_by_email(session, email) is not None:
        raise click.ClickException(f"User {email} already exists")

    auth_method = AuthMethod(auth.upper())
    password = (
        _prompt_new_password("Password") if auth_method is AuthMethod.LOCAL else None
    )
    try:
        user = create_user(
            session,
            email,
            password,
            auth_method=auth_method,
            min_password_length=_min_password_length(),
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
    admin = _require_admin(session, admin_email, "Set auth method")
    user = _require_user(session, email)

    auth_method = AuthMethod(auth.upper())
    password = (
        _prompt_new_password("New password")
        if auth_method is AuthMethod.LOCAL
        else None
    )
    set_auth_method(
        session,
        user,
        auth_method,
        password=password,
        min_password_length=_min_password_length(),
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
    admin = _require_admin(session, admin_email, "Password reset")
    user = _require_user(session, email)
    if user.auth_method is AuthMethod.GOOGLE:
        raise click.ClickException(
            f"{user.email} logs in with Google and has no password: "
            "set-auth-method makes them local, with a new password"
        )

    password = _prompt_new_password("New password")
    set_password(session, user, password, min_password_length=_min_password_length())
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
    admin = _require_admin(session, admin_email, "Promote")
    user = _require_user(session, email)
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
    admin = _require_admin(session, admin_email, "Demote")
    user = _require_user(session, email)
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
    admin = _require_admin(session, admin_email, "Lock")
    user = _require_user(session, email)
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
    admin = _require_admin(session, admin_email, "Unlock")
    user = _require_user(session, email)

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
    admin = _require_admin(session, admin_email, "Delete")
    user = _require_user(session, email)
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


@click.command("copy-collection")
@click.argument("source_email")
@click.argument("target_email")
@click.option(
    "--mode",
    type=click.Choice([m.value.lower() for m in CopyMode], case_sensitive=False),
    default=CopyMode.KEEP.value.lower(),
    show_default=True,
    help=(
        "Books the target already has: keep theirs (add only the books they lack), "
        "add these copies to theirs (up to 999), or replace their whole collection."
    ),
)
@admin_email_option
@click.option("--yes", is_flag=True, help="Do not ask for confirmation (replace).")
@with_appcontext
def copy_collection_command(
    source_email: str, target_email: str, mode: str, admin_email: str, yes: bool
) -> None:
    """
    Copy a user's collection into another user's. Copied books keep their
    copies and start unread; the source's collection is unchanged. Requires
    admin credentials.
    """
    session = get_session()
    admin = _require_admin(session, admin_email, "Copy collection")
    source = _require_user(session, source_email)
    target = _require_user(session, target_email)
    if source.id == target.id:
        raise click.ClickException("A collection cannot be copied to its own user")
    copy_mode = CopyMode(mode.upper())
    if copy_mode is CopyMode.REPLACE and not yes:
        click.confirm(
            f"Replace the collection of {target.email} with that of {source.email}? "
            "Their books are removed first",
            abort=True,
        )

    copied = copy_collection(session, source.id, target.id, copy_mode)
    current_app.logger.info(
        "Collection of %s copied to %s (%s) by admin %s: %s",
        source.email,
        target.email,
        copy_mode.value.lower(),
        admin.email,
        copied,
    )
    click.echo(
        f"Collection of {source.email} copied to {target.email}: "
        f"{copied.summary(copy_mode)}."
    )


@click.command("list-users")
@admin_email_option
@with_appcontext
def list_users_command(admin_email: str) -> None:
    """List all users. Requires admin credentials."""
    session = get_session()
    _require_admin(session, admin_email, "List users")

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


def init_app(app: Flask) -> None:
    app.cli.add_command(init_db_command)
    app.cli.add_command(prune_login_failures_command)
    app.cli.add_command(setup_token_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(create_user_command)
    app.cli.add_command(reset_user_password_command)
    app.cli.add_command(set_auth_method_command)
    app.cli.add_command(promote_user_command)
    app.cli.add_command(demote_user_command)
    app.cli.add_command(lock_user_command)
    app.cli.add_command(unlock_user_command)
    app.cli.add_command(delete_user_command)
    app.cli.add_command(list_users_command)
    app.cli.add_command(copy_collection_command)
