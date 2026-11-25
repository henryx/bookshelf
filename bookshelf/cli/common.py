"""What the commands share: checking an email, asking for passwords, admin credentials."""

import click
from email_validator import EmailNotValidError, validate_email
from flask import current_app
from sqlalchemy.orm import Session

from bookshelf.models.users import (
    PasswordTooShortError,
    User,
    authenticate,
    find_by_email,
    validate_new_password,
)

__all__ = [
    "admin_email_option",
    "min_password_length",
    "prompt_new_password",
    "require_admin",
    "require_user",
    "valid_email",
]


def valid_email(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    """Same rules as the login form, so every new account can log in."""
    try:
        validate_email(value, check_deliverability=False)
    except EmailNotValidError as e:
        raise click.BadParameter(str(e)) from e
    return value


def min_password_length() -> int:
    return current_app.config["MIN_PASSWORD_LENGTH"]


def prompt_new_password(label: str) -> str:
    """Prompt (with confirmation) until the password is long enough."""
    while True:
        password = click.prompt(label, hide_input=True, confirmation_prompt=True)
        try:
            validate_new_password(password, min_length=min_password_length())
        except PasswordTooShortError as e:
            click.echo(f"Error: {e}", err=True)
            continue
        return password


def require_admin(session: Session, admin_email: str, action: str) -> User:
    """
    Prompt for the admin password and return the admin, or abort.
    """
    admin_password = click.prompt("Admin password", hide_input=True)
    admin = authenticate(session, admin_email, admin_password)
    if admin is None or not admin.is_admin:
        current_app.logger.warning("%s refused for admin %s", action, admin_email)
        raise click.ClickException("Invalid admin credentials")
    return admin


def require_user(session: Session, email: str) -> User:
    user = find_by_email(session, email)
    if user is None:
        raise click.ClickException(f"User {email} not found")
    return user


admin_email_option = click.option(
    "--admin-email", prompt="Admin email", help="Admin authorising the operation."
)
