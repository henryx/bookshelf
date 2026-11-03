from flask import current_app
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    EmailField,
    PasswordField,
    SelectField,
    ValidationError,
)
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional

from bookshelf.models.users import PasswordTooShortError, Role, validate_new_password

__all__ = ["LoginForm", "NewAccountForm", "UserForm"]


def _strip(value: str | None) -> str | None:
    return value.strip() if value else value


def _email_field() -> EmailField:
    return EmailField(
        "Email",
        filters=[_strip],
        validators=[DataRequired(), Length(max=254), Email()],
    )


def _check_new_password(password: str) -> None:
    try:
        validate_new_password(
            password, min_length=current_app.config["MIN_PASSWORD_LENGTH"]
        )
    except PasswordTooShortError as e:
        raise ValidationError(str(e)) from e


class LoginForm(FlaskForm):
    email = _email_field()
    password = PasswordField("Password", validators=[DataRequired()])


class NewAccountForm(FlaskForm):
    """A new account: email and a password typed twice, of the configured length."""

    email = _email_field()
    password = PasswordField("Password", validators=[DataRequired()])
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", "Passwords must match")],
    )

    def validate_password(self, field: PasswordField) -> None:
        _check_new_password(field.data or "")


class UserForm(FlaskForm):
    """An existing user's editable details; an empty password keeps the old one."""

    role = SelectField(
        "Role", choices=[(Role.REGULAR.value, "Regular"), (Role.ADMIN.value, "Admin")]
    )
    locked = BooleanField("Locked (cannot log in)")
    # Optional stops validation of an empty password, so validate_password
    # only checks a password that was typed.
    password = PasswordField("New password", validators=[Optional()])
    confirm = PasswordField(
        "Confirm new password",
        validators=[EqualTo("password", "Passwords must match")],
    )

    def validate_password(self, field: PasswordField) -> None:
        _check_new_password(field.data or "")
