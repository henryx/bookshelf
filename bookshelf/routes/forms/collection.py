"""A user's collection: copies, read, CSV import, and copying it to another user."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, RadioField, SelectChoice, SelectField
from wtforms.validators import InputRequired

from bookshelf.models.books import CopyMode
from bookshelf.routes.forms.fields import copies_field

__all__ = ["CopiesForm", "CopyCollectionForm", "ImportForm", "ReadForm"]


class CopiesForm(FlaskForm):
    """How many copies of a book the current user owns."""

    copies = copies_field("Copies")


class ReadForm(FlaskForm):
    """Whether the current user has read a book in their collection."""

    read = BooleanField("Read")


class ImportForm(FlaskForm):
    """A CSV file of books to add to the current user's collection."""

    file = FileField(
        "CSV file",
        validators=[
            FileRequired("Choose a CSV file to import."),
            FileAllowed(["csv"], "Choose a .csv file."),
        ],
    )


class CopyCollectionForm(FlaskForm):
    """
    Copy a user's collection into another user's: the route sets the target
    choices (every other user, after an empty "choose" one).
    """

    target = SelectField("Copy to", validators=[InputRequired("Choose a user.")])
    mode = RadioField(
        "Books they already have",
        choices=[
            SelectChoice(
                CopyMode.KEEP.value, "Keep theirs: add only the books they lack"
            ),
            SelectChoice(CopyMode.ADD.value, "Add these copies to theirs"),
            SelectChoice(
                CopyMode.REPLACE.value, "Replace their whole collection with this one"
            ),
        ],
        default=CopyMode.KEEP.value,
    )
