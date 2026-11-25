"""Reading one imported row: a book, with the user's copies, read and score."""

import re
from dataclasses import dataclass

from bookshelf.models.books import (
    CATEGORY_MAX_LENGTH,
    MAX_SCORE,
    Author,
    InvalidIsbnError,
    IsbnLostDigitsError,
    NewBook,
    normalise_isbn,
    unique_authors,
    unique_categories,
)
from bookshelf.models.collection_csv.columns import SEPARATOR, unguard

__all__ = ["Row", "RowError", "parse_row"]

_MAX_COPIES = 999
# Column sizes in BOOKS and AUTHORS (categories: CATEGORY_MAX_LENGTH).
_MAX_LENGTHS = {
    "title": 255,
    "publisher": 255,
    "series": 255,
    "volume": 32,
    "plot": 4000,
}
_AUTHOR_MAX_LENGTH = 127
_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0", ""}
# A year in a date: four digits on their own ("1991-05-12", "12/05/1991").
_DATE_YEAR = re.compile(r"(?<!\d)\d{4}(?!\d)")


class RowError(ValueError):
    """A row that cannot be imported; the others still are."""


@dataclass(frozen=True)
class Row:
    book: NewBook
    copies: int
    read: bool
    score: int | None = None


def _text(row: dict[str, str], column: str) -> str | None:
    """A column's trimmed text, None when blank; RowError when too long."""
    value = unguard((row.get(column) or "").strip()) or None
    max_length = _MAX_LENGTHS.get(column)
    if value and max_length and len(value) > max_length:
        raise RowError(f"the {column} cannot be longer than {max_length} characters.")
    return value


def _number(text: str | None, low: int, high: int) -> int | None:
    """`text` as a whole number from low to high; None if it is not one."""
    if text is None or not text.isdigit() or not low <= int(text) <= high:
        return None
    return int(text)


def _year(text: str | None) -> int | None:
    """
    The publication year: a whole number, or the year of a date (its only
    four-digit number); RowError when there is no single year in it.
    """
    if text is None:
        return None
    years = [text] if text.isdigit() else _DATE_YEAR.findall(text)
    year = _number(years[0], 1, 9999) if len(years) == 1 else None
    if year is None:
        raise RowError(
            "the year must be a whole number from 1 to 9999, or a date with it."
        )
    return year


def _authors(text: str | None) -> tuple[Author, ...]:
    """
    "Name Surname" authors separated by commas: the last word is the surname
    ("Homer" alone has only one), so "Ursula K. Le Guin" gives "Guin".
    """
    authors = []
    for part in (text or "").split(SEPARATOR):
        words = part.split()
        if not words:
            continue
        author = Author(" ".join(words[:-1]), words[-1])
        if max(len(author.name), len(author.surname)) > _AUTHOR_MAX_LENGTH:
            raise RowError(
                f"author names cannot be longer than {_AUTHOR_MAX_LENGTH} characters."
            )
        authors.append(author)
    return tuple(unique_authors(tuple(authors)))


def _categories(text: str | None) -> tuple[str, ...]:
    categories = unique_categories(tuple((text or "").split(SEPARATOR)))
    if any(len(category) > CATEGORY_MAX_LENGTH for category in categories):
        raise RowError(
            f"categories cannot be longer than {CATEGORY_MAX_LENGTH} characters."
        )
    return tuple(categories)


def _isbn(text: str | None) -> str | None:
    """
    The ISBN-13 of an ISBN cell, tolerating what spreadsheets and copying do
    to it (see normalise_isbn). RowError when it is not a valid ISBN, or
    was written in scientific notation.
    """
    if text is None:
        return None
    try:
        return normalise_isbn(text)
    except IsbnLostDigitsError as e:
        raise RowError(
            f"the ISBN became a number in a spreadsheet ({text}), which lost its "
            "digits: format the column as text and enter the ISBN again."
        ) from e
    except InvalidIsbnError as e:
        raise RowError(f"not a valid ISBN ({text}).") from e


def _score(text: str | None) -> int | None:
    """The user's score: None when blank; RowError when not 1 to MAX_SCORE."""
    if text is None:
        return None
    score = _number(text, 1, MAX_SCORE)
    if score is None:
        raise RowError(f"the score must be a whole number from 1 to {MAX_SCORE}.")
    return score


def parse_row(row: dict[str, str]) -> Row:
    """A CSV row as a book with the user's copies, read and score; RowError if invalid."""
    title = _text(row, "title")
    if title is None:
        raise RowError("the title is required.")
    isbn = _isbn(_text(row, "isbn"))
    year = _year(_text(row, "year"))
    copies = _number(_text(row, "copies") or "1", 1, _MAX_COPIES)
    if copies is None:
        raise RowError(f"copies must be a whole number from 1 to {_MAX_COPIES}.")
    read = (_text(row, "read") or "").casefold()
    if read not in _YES | _NO:
        raise RowError("read must be yes or no.")
    score = _score(_text(row, "score"))
    book = NewBook(
        title=title,
        authors=_authors(_text(row, "authors")),
        isbn=isbn,
        publisher=_text(row, "publisher"),
        publication_year=year,
        series=_text(row, "series"),
        volume=_text(row, "volume"),
        plot=_text(row, "plot"),
        categories=_categories(_text(row, "categories")),
    )
    return Row(book, copies, read in _YES, score)
