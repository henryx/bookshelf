from flask import current_app
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    EmailField,
    FieldList,
    Form,
    FormField,
    IntegerField,
    PasswordField,
    SelectChoice,
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

from bookshelf.models.books import Author, InvalidIsbnError, NewBook, normalise_isbn
from bookshelf.models.users import PasswordTooShortError, Role, validate_new_password

__all__ = [
    "AccountForm",
    "AuthorForm",
    "BookForm",
    "CopiesForm",
    "EditBookForm",
    "LoginForm",
    "NewAccountForm",
    "NewUserForm",
    "RenameAuthorForm",
    "UserForm",
]

# Column sizes in BOOKS, AUTHORS, BOOK_TAGS and USERS.
_NAME_MAX_LENGTH = 255
_AUTHOR_NAME_MAX_LENGTH = 127
_TAG_MAX_LENGTH = 64
_PERSON_NAME_MAX_LENGTH = 127


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


class NewUserForm(NewAccountForm):
    """A user created by an admin: a new account, with optional name and surname."""

    name = _optional_text("Name", _PERSON_NAME_MAX_LENGTH)
    surname = _optional_text("Surname", _PERSON_NAME_MAX_LENGTH)


class UserForm(FlaskForm):
    """An existing user's editable details; an empty password keeps the old one."""

    name = _optional_text("Name", _PERSON_NAME_MAX_LENGTH)
    surname = _optional_text("Surname", _PERSON_NAME_MAX_LENGTH)
    role = SelectField(
        "Role",
        choices=[
            SelectChoice(Role.REGULAR.value, "Regular"),
            SelectChoice(Role.ADMIN.value, "Admin"),
        ],
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


class AuthorForm(Form):
    """
    One author row: name and surname. An empty row is ignored; a name needs a
    surname. A plain Form: the BookForm around it has the CSRF token.
    """

    name = StringField(
        "Name", filters=[_strip], validators=[Length(max=_AUTHOR_NAME_MAX_LENGTH)]
    )
    surname = StringField(
        "Surname", filters=[_strip], validators=[Length(max=_AUTHOR_NAME_MAX_LENGTH)]
    )

    def validate_surname(self, field: StringField) -> None:
        if self.name.data and not field.data:
            raise ValidationError("Surname is required.")

    def to_author(self) -> Author:
        return Author(self.name.data or "", self.surname.data or "")


class RenameAuthorForm(FlaskForm):
    """An author's new name (optional, as for "Homer") and surname."""

    name = StringField(
        "Name", filters=[_strip], validators=[Length(max=_AUTHOR_NAME_MAX_LENGTH)]
    )
    surname = StringField(
        "Surname",
        filters=[_strip],
        validators=[DataRequired(), Length(max=_AUTHOR_NAME_MAX_LENGTH)],
    )

    def to_author(self) -> Author:
        return Author(self.name.data or "", self.surname.data or "")


# Copies of a book one user may record owning.
_MAX_COPIES = 999


def _copies_field(label: str) -> IntegerField:
    return IntegerField(
        label,
        default=1,
        validators=[InputRequired(), NumberRange(min=1, max=_MAX_COPIES)],
    )


class CopiesForm(FlaskForm):
    """How many copies of a book the current user owns."""

    copies = _copies_field("Copies")


class EditBookForm(FlaskForm):
    """
    A book's editable fields: authors as rows of name and surname, ISBN
    (while it has none, or for admins), publisher, year, series, volume, plot and
    tags separated by commas. BookForm adds a new book's title and copies.
    """

    authors = FieldList(FormField(AuthorForm), "Authors", min_entries=1)
    isbn = StringField("ISBN", filters=[_strip_or_none])
    publisher = _optional_text("Publisher", _NAME_MAX_LENGTH)
    publication_year = IntegerField(
        "Year", validators=[Optional(), NumberRange(min=1, max=9999)]
    )
    series = _optional_text("Series", _NAME_MAX_LENGTH)
    volume = _optional_text("Volume", 32)
    tags = StringField("Tags")
    plot = TextAreaField(
        "Plot", filters=[_strip_or_none], validators=[Optional(), Length(max=4000)]
    )

    def add_author_row(self) -> None:
        """One more empty author row (the + button without JavaScript)."""
        self.authors.append_entry()

    def remove_author_row(self, index: str) -> None:
        """Drop the author row numbered `index` (the - button without JavaScript)."""
        name = f"{self.authors.name}-{index}"
        self.authors.entries = [e for e in self.authors.entries if e.name != name]
        if not self.authors.entries:
            self.authors.append_entry()

    def validate_authors(self, field: FieldList) -> None:
        """
        Refuse an author listed twice (same name and surname, ignoring case):
        the repeated row is marked. Empty rows are not authors.
        """
        seen: set[tuple[str, str]] = set()
        repeated = False
        for entry in field.entries:
            author = entry.form.to_author()
            key = (author.name.casefold(), author.surname.casefold())
            if not any(key):
                continue
            if key in seen:
                entry.form.surname.errors.append("This author is already listed.")
                repeated = True
            seen.add(key)
        if repeated:
            raise ValidationError("Each author can be listed only once.")

    def author_list(self) -> tuple[Author, ...]:
        """The author rows as typed, empty ones included (see unique_authors)."""
        return tuple(entry.form.to_author() for entry in self.authors)

    def tag_names(self) -> tuple[str, ...]:
        return tuple((self.tags.data or "").split(","))

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


class BookForm(EditBookForm):
    """A new book: the editable fields, and its title and the copies the user owns."""

    title = StringField(
        "Title",
        filters=[_strip],
        validators=[DataRequired(), Length(max=_NAME_MAX_LENGTH)],
    )
    copies = _copies_field("Copies you own")

    def to_book(self) -> NewBook:
        return NewBook(
            title=self.title.data or "",
            authors=self.author_list(),
            isbn=self.isbn.data,
            publisher=self.publisher.data,
            publication_year=self.publication_year.data,
            series=self.series.data,
            volume=self.volume.data,
            plot=self.plot.data,
            tags=self.tag_names(),
        )


class AccountForm(FlaskForm):
    """
    A user's own account: name, surname and, optionally, a new password,
    which needs the current one. Whether the current password is right is
    checked against the database by the view.
    """

    name = _optional_text("Name", _PERSON_NAME_MAX_LENGTH)
    surname = _optional_text("Surname", _PERSON_NAME_MAX_LENGTH)
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
        _check_new_password(field.data or "")
