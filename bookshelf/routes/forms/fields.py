"""Field builders, filters and checks the forms share."""

from flask import current_app
from wtforms import EmailField, IntegerField, StringField, ValidationError
from wtforms.validators import (
    DataRequired,
    Email,
    InputRequired,
    Length,
    NumberRange,
    Optional,
)

from bookshelf.models.books import MAX_COPIES
from bookshelf.models.users import PasswordTooShortError, validate_new_password

__all__ = [
    "AUTHOR_NAME_MAX_LENGTH",
    "NAME_MAX_LENGTH",
    "PERSON_NAME_MAX_LENGTH",
    "check_new_password",
    "copies_field",
    "email_field",
    "optional_text",
    "strip",
    "strip_or_none",
]

# Column sizes in BOOKS, AUTHORS and USERS.
NAME_MAX_LENGTH = 255
AUTHOR_NAME_MAX_LENGTH = 127
PERSON_NAME_MAX_LENGTH = 127


def strip(value: str | None) -> str | None:
    return value.strip() if value else value


def strip_or_none(value: str | None) -> str | None:
    """Trimmed text, or None when blank, so empty fields are stored as NULL."""
    return (value or "").strip() or None


def optional_text(label: str, max_length: int) -> StringField:
    return StringField(
        label, filters=[strip_or_none], validators=[Optional(), Length(max=max_length)]
    )


def email_field() -> EmailField:
    return EmailField(
        "Email",
        filters=[strip],
        validators=[DataRequired(), Length(max=254), Email()],
    )


def check_new_password(password: str) -> None:
    try:
        validate_new_password(
            password, min_length=current_app.config["MIN_PASSWORD_LENGTH"]
        )
    except PasswordTooShortError as e:
        raise ValidationError(str(e)) from e


def copies_field(label: str) -> IntegerField:
    return IntegerField(
        label,
        default=1,
        validators=[InputRequired(), NumberRange(min=1, max=MAX_COPIES)],
    )
