from flask import current_app
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    EmailField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
    ValidationError,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
)

from bookshelf.models.books import InvalidIsbnError, NewBook, normalise_isbn
from bookshelf.models.users import PasswordTooShortError, Role, validate_new_password

__all__ = ["BookForm", "LoginForm", "NewAccountForm", "UserForm"]

# Column sizes in BOOKS, AUTHORS and BOOK_TAGS.
_NAME_MAX_LENGTH = 255
_TAG_MAX_LENGTH = 64


def _strip(value: str | None) -> str | None:
    return value.strip() if value else value


def _strip_or_none(value: str | None) -> str | None:
    """Trimmed text, or None when blank, so empty fields are stored as NULL."""
    return (value or "").strip() or None


def _optional_text(label: str, max_length: int) -> StringField:
    return StringField(
        label, filters=[_strip_or_none], validators=[Optional(), Length(max=max_length)]
    )


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


class BookForm(FlaskForm):
    """A new book. Authors are one per line, tags separated by commas."""

    title = StringField(
        "Title",
        filters=[_strip],
        validators=[DataRequired(), Length(max=_NAME_MAX_LENGTH)],
    )
    authors = TextAreaField("Authors")
    isbn = StringField("ISBN", filters=[_strip_or_none])
    publisher = _optional_text("Publisher", _NAME_MAX_LENGTH)
    publication_year = IntegerField(
        "Year", validators=[Optional(), NumberRange(min=1, max=9999)]
    )
    language = _optional_text("Language", 64)
    tags = StringField("Tags")
    notes = TextAreaField(
        "Notes", filters=[_strip_or_none], validators=[Optional(), Length(max=4000)]
    )
    copies = IntegerField(
        "Copies you own",
        default=1,
        validators=[InputRequired(), NumberRange(min=1, max=999)],
    )

    def author_names(self) -> tuple[str, ...]:
        return tuple((self.authors.data or "").splitlines())

    def tag_names(self) -> tuple[str, ...]:
        return tuple((self.tags.data or "").split(","))

    def validate_authors(self, _field: TextAreaField) -> None:
        if any(len(name.strip()) > _NAME_MAX_LENGTH for name in self.author_names()):
            raise ValidationError(
                f"Author names cannot be longer than {_NAME_MAX_LENGTH} characters."
            )

    def validate_tags(self, _field: StringField) -> None:
        if any(len(tag.strip()) > _TAG_MAX_LENGTH for tag in self.tag_names()):
            raise ValidationError(
                f"Tags cannot be longer than {_TAG_MAX_LENGTH} characters."
            )

    def validate_isbn(self, field: StringField) -> None:
        if not field.data:
            return
        try:
            normalise_isbn(field.data)
        except InvalidIsbnError as e:
            raise ValidationError("Not a valid ISBN.") from e

    def to_book(self) -> NewBook:
        return NewBook(
            title=self.title.data or "",
            authors=self.author_names(),
            isbn=self.isbn.data,
            publisher=self.publisher.data,
            publication_year=self.publication_year.data,
            language=self.language.data,
            notes=self.notes.data,
            tags=self.tag_names(),
        )
