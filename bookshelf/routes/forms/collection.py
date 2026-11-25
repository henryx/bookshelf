"""A user's collection: copies, read, score, CSV import, and copying it to another user."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, IntegerField, RadioField, SelectChoice, SelectField
from wtforms.validators import InputRequired, NumberRange

from bookshelf.models.books import MAX_SCORE, CopyMode
from bookshelf.routes.forms.fields import copies_field

__all__ = ["CopiesForm", "CopyCollectionForm", "ImportForm", "ReadForm", "ScoreForm"]


class CopiesForm(FlaskForm):
    """How many copies of a book the current user owns."""

    copies = copies_field("Copies")


class ReadForm(FlaskForm):
    """Whether the current user has read a book in their collection."""

    read = BooleanField("Read")


class ScoreForm(FlaskForm):
    """The star the current user clicked to score a book in their collection."""

    score = IntegerField(
        "Score",
        validators=[
            InputRequired("Choose a score."),
            NumberRange(1, MAX_SCORE, f"A score is from 1 to {MAX_SCORE}."),
        ],
    )


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
