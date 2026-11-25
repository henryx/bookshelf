"""Logging in, the first admin, users (admins) and the user's own account."""

from flask import current_app, request
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    PasswordField,
    SelectChoice,
    SelectField,
    StringField,
    ValidationError,
)
from wtforms.validators import DataRequired, EqualTo, Optional

from bookshelf.models.users import AuthMethod, Role
from bookshelf.routes.forms.fields import (
    PERSON_NAME_MAX_LENGTH,
    check_new_password,
    email_field,
    optional_text,
)
from bookshelf.setup_token import token_matches

__all__ = [
    "AccountForm",
    "LoginForm",
    "NewAccountForm",
    "NewUserForm",
    "SetupForm",
    "UserForm",
]


class LoginForm(FlaskForm):
    email = email_field()
    password = PasswordField("Password", validators=[DataRequired()])


class NewAccountForm(FlaskForm):
    """A new account: email and a password typed twice, of the configured length."""

    email = email_field()
    password = PasswordField("Password", validators=[DataRequired()])
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", "Passwords must match")],
    )

    def validate_password(self, field: PasswordField) -> None:
        check_new_password(field.data or "")


class SetupForm(NewAccountForm):
    """The first admin, created by whoever has the setup token."""

    setup_token = StringField("Setup token", validators=[DataRequired()])

    def validate_setup_token(self, field: StringField) -> None:
        if not token_matches(field.data or "", current_app.config["SECRET_KEY"]):
            current_app.logger.warning("Wrong setup token from %s", request.remote_addr)
            raise ValidationError("Wrong setup token.")


NO_GOOGLE_PASSWORD = "A user logging in with Google has no password: leave it empty."


def _auth_method_field() -> SelectField:
    return SelectField(
        "Logs in",
        choices=[
            SelectChoice(AuthMethod.LOCAL.value, "With a password"),
            SelectChoice(AuthMethod.GOOGLE.value, "With Google"),
        ],
        default=AuthMethod.LOCAL.value,
    )


class NewUserForm(NewAccountForm):
    """
    A user created by an admin: a new account, with optional name and
    surname, logging in with a password (required) or with Google (none).
    """

    name = optional_text("Name", PERSON_NAME_MAX_LENGTH)
    surname = optional_text("Surname", PERSON_NAME_MAX_LENGTH)
    auth_method = _auth_method_field()
    password = PasswordField("Password")
    confirm = PasswordField(
        "Confirm password", validators=[EqualTo("password", "Passwords must match")]
    )

    def validate_password(self, field: PasswordField) -> None:
        if self.auth_method.data == AuthMethod.GOOGLE.value:
            if field.data:
                raise ValidationError(NO_GOOGLE_PASSWORD)
            return
        if not field.data:
            raise ValidationError("This field is required.")
        check_new_password(field.data)


class UserForm(FlaskForm):
    """An existing user's editable details; an empty password keeps the old one."""

    name = optional_text("Name", PERSON_NAME_MAX_LENGTH)
    surname = optional_text("Surname", PERSON_NAME_MAX_LENGTH)
    role = SelectField(
        "Role",
        choices=[
            SelectChoice(Role.REGULAR.value, "Regular"),
            SelectChoice(Role.ADMIN.value, "Admin"),
        ],
    )
    locked = BooleanField("Locked (cannot log in)")
    # A user switched from Google to a password needs one: update_user says so.
    auth_method = _auth_method_field()
    # Optional stops validation of an empty password, so validate_password
    # only checks a password that was typed.
    password = PasswordField("New password", validators=[Optional()])
    confirm = PasswordField(
        "Confirm new password",
        validators=[EqualTo("password", "Passwords must match")],
    )

    def validate_password(self, field: PasswordField) -> None:
        if self.auth_method.data == AuthMethod.GOOGLE.value:
            raise ValidationError(NO_GOOGLE_PASSWORD)
        check_new_password(field.data or "")


class AccountForm(FlaskForm):
    """
    A user's own account: name, surname and, optionally, a new password,
    which needs the current one. Whether the current password is right is
    checked against the database by the view.
    """

    name = optional_text("Name", PERSON_NAME_MAX_LENGTH)
    surname = optional_text("Surname", PERSON_NAME_MAX_LENGTH)
    current_password = PasswordField("Current password")
    password = PasswordField("New password", validators=[Optional()])
    confirm = PasswordField(
        "Confirm new password",
        validators=[EqualTo("password", "Passwords must match")],
    )

    def validate_current_password(self, field: PasswordField) -> None:
        if self.password.data and not field.data:
            raise ValidationError("Enter your current password to change it.")

    def validate_password(self, field: PasswordField) -> None:
        check_new_password(field.data or "")
