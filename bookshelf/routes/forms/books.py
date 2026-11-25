"""A book's forms (adding, editing) and renaming an author."""

from collections.abc import Sequence

from flask_wtf import FlaskForm
from wtforms import (
    Field,
    FieldList,
    Form,
    FormField,
    IntegerField,
    StringField,
    TextAreaField,
    ValidationError,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from bookshelf.models.books import (
    CATEGORY_MAX_LENGTH,
    MAX_COPIES,
    Author,
    FoundBook,
    InvalidIsbnError,
    IsbnLostDigitsError,
    NewBook,
    normalise_isbn,
)
from bookshelf.routes.forms.fields import (
    AUTHOR_NAME_MAX_LENGTH,
    NAME_MAX_LENGTH,
    copies_field,
    optional_text,
    strip,
    strip_or_none,
)

__all__ = ["AuthorForm", "BookForm", "EditBookForm", "RenameAuthorForm"]


class AuthorForm(Form):
    """
    One author row: name and surname. An empty row is ignored; a name needs a
    surname. A plain Form: the BookForm around it has the CSRF token.
    """

    name = StringField(
        "Name", filters=[strip], validators=[Length(max=AUTHOR_NAME_MAX_LENGTH)]
    )
    surname = StringField(
        "Surname", filters=[strip], validators=[Length(max=AUTHOR_NAME_MAX_LENGTH)]
    )

    def validate_surname(self, field: StringField) -> None:
        if self.name.data and not field.data:
            raise ValidationError("Surname is required.")

    def to_author(self) -> Author:
        return Author(self.name.data or "", self.surname.data or "")


class RenameAuthorForm(FlaskForm):
    """An author's new name (optional, as for "Homer") and surname."""

    name = StringField(
        "Name", filters=[strip], validators=[Length(max=AUTHOR_NAME_MAX_LENGTH)]
    )
    surname = StringField(
        "Surname",
        filters=[strip],
        validators=[DataRequired(), Length(max=AUTHOR_NAME_MAX_LENGTH)],
    )

    def to_author(self) -> Author:
        return Author(self.name.data or "", self.surname.data or "")


class EditBookForm(FlaskForm):
    """
    A book's editable fields: authors as rows of name and surname, ISBN
    (while it has none, or for admins), publisher, year, series, volume, plot and
    categories, one per row; and the copies the user owns, while the book is
    in their collection (left out, they stay as they are). BookForm adds a new
    book's title and makes the copies required.
    """

    authors = FieldList(FormField(AuthorForm), "Authors", min_entries=1)
    isbn = StringField("ISBN or ISSN", filters=[strip_or_none])
    publisher = optional_text("Publisher", NAME_MAX_LENGTH)
    publication_year = IntegerField(
        "Year", validators=[Optional(), NumberRange(min=1, max=9999)]
    )
    series = optional_text("Series", NAME_MAX_LENGTH)
    volume = optional_text("Volume", 32)
    categories = FieldList(
        StringField(
            "Category",
            filters=[strip],
            validators=[
                Length(
                    max=CATEGORY_MAX_LENGTH,
                    message=f"Categories cannot be longer than {CATEGORY_MAX_LENGTH} "
                    "characters.",
                )
            ],
        ),
        "Categories",
        min_entries=1,
    )
    plot = TextAreaField(
        "Plot", filters=[strip_or_none], validators=[Optional(), Length(max=4000)]
    )
    copies = IntegerField(
        "Copies you own", validators=[Optional(), NumberRange(min=1, max=MAX_COPIES)]
    )

    def add_row(self, field: FieldList) -> None:
        """One more empty row of authors or categories (the + button without JavaScript)."""
        field.append_entry()

    def remove_row(self, field: FieldList, index: str) -> None:
        """Drop the row numbered `index` (the - button without JavaScript)."""
        name = f"{field.name}-{index}"
        field.entries = [e for e in field.entries if e.name != name]
        if not field.entries:
            field.append_entry()

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

    def category_names(self) -> tuple[str, ...]:
        """The category rows as typed, empty ones included (see unique_categories)."""
        return tuple(entry.data or "" for entry in self.categories)

    def validate_isbn(self, field: StringField) -> None:
        if not field.data:
            return
        try:
            normalise_isbn(field.data)
        except IsbnLostDigitsError as e:
            raise ValidationError(
                "This code became a number in a spreadsheet, which lost its digits: "
                "enter it again."
            ) from e
        except InvalidIsbnError as e:
            raise ValidationError("Not a valid ISBN or ISSN.") from e


class BookForm(EditBookForm):
    """A new book: the editable fields, and its title and the copies the user owns."""

    title = StringField(
        "Title",
        filters=[strip],
        validators=[DataRequired(), Length(max=NAME_MAX_LENGTH)],
    )
    copies = copies_field("Copies you own")

    def fill_empty(self, found: FoundBook) -> list[str]:
        """
        Fill the fields left empty with a book found online (the Look up
        button); what was typed stays. The fields filled, as named to people.
        """
        filled = ["title"] if _fill(self.title, found.title) else []
        if found.authors and not any(a.name or a.surname for a in self.author_list()):
            _replace_rows(
                self.authors,
                [{"name": a.name, "surname": a.surname} for a in found.authors],
            )
            filled.append("authors")
        filled += [
            field.label.text.lower()
            for field, value in (
                (self.publisher, found.publisher),
                (self.publication_year, found.publication_year),
                (self.plot, found.plot),
            )
            if _fill(field, value)
        ]
        if found.categories and not any(self.category_names()):
            _replace_rows(self.categories, found.categories)
            filled.append("categories")
        return filled

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
            categories=self.category_names(),
        )


def _fill(field: Field, value: object) -> bool:
    """Give an empty field this value, if there is one; whether it did."""
    if field.data not in (None, "") or value is None:
        return False
    field.data = value
    # What was posted, shown in place of the data: it was empty.
    field.raw_data = None
    return True


def _replace_rows(field: FieldList, rows: Sequence[object]) -> None:
    """Replace every row of authors or categories with these."""
    while field.entries:
        field.pop_entry()
    for row in rows:
        field.append_entry(row)
