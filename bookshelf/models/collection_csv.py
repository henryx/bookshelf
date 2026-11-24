"""A user's collection as CSV: exporting it, and importing books into it."""

import csv
import io
import re
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from bookshelf.db.orm import (
    AuthorRow,
    BookAuthorRow,
    BookCategoryRow,
    BookRow,
    CollectionBookRow,
)
from bookshelf.models.books import (
    CATEGORY_MAX_LENGTH,
    Author,
    Book,
    BookExistsError,
    InvalidIsbnError,
    IsbnLostDigitsError,
    NewBook,
    UnknownBookError,
    add_book,
    add_to_collection,
    find_book,
    find_book_by_isbn,
    normalise_isbn,
    unique_authors,
    unique_categories,
)

__all__ = [
    "COLUMNS",
    "MAX_ROWS",
    "CsvFileError",
    "ImportReport",
    "export_collection",
    "import_collection",
]

# The export's columns, in order; an import may have any of them, in any
# order, but needs title. authors ("Name Surname", or "Surname" alone) and
# categories are separated by ", ". read: yes or no.
COLUMNS = (
    "title",
    "authors",
    "isbn",
    "publisher",
    "year",
    "series",
    "volume",
    "plot",
    "categories",
    "copies",
    "read",
)
# Books one import may hold: each is a few queries and a commit, and the
# request has to end before the worker timeout.
MAX_ROWS = 1000
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
# Between authors and between categories alike.
_SEPARATOR = ","
_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0", ""}
# A spreadsheet runs a cell starting with one of these as a formula: the
# export puts a ' before it (shown as text), and the import takes it off.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
# A year in a date: four digits on their own ("1991-05-12", "12/05/1991").
_DATE_YEAR = re.compile(r"(?<!\d)\d{4}(?!\d)")


class CsvFileError(ValueError):
    """A file that cannot be imported at all (no rows were imported)."""


class _RowError(ValueError):
    """A row that cannot be imported; the others still are."""


@dataclass
class ImportReport:
    """
    What an import did. added: books that went into the collection, created
    of them new to the catalogue; skipped: books already in the collection;
    errors: one message per row not imported, with its row number as in a
    spreadsheet (the header is row 1); ignored_columns: the file's columns
    that are not in COLUMNS, as named in the file.
    """

    added: int = 0
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Row:
    book: NewBook
    copies: int
    read: bool


def _guard(text: str) -> str:
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def _unguard(text: str) -> str:
    return (
        text[1:] if text[:1] == "'" and text[1:].startswith(_FORMULA_STARTS) else text
    )


def export_collection(session: Session, user_id: UUID) -> str:
    """
    The user's collection as CSV text with COLUMNS, by title, starting with a
    byte order mark so that spreadsheets read it as UTF-8.
    """
    # The user's books, then the authors and categories of all of them.
    owned = CollectionBookRow.user_id == user_id
    books = session.execute(
        select(
            BookRow.id,
            BookRow.title,
            BookRow.isbn,
            BookRow.publisher,
            BookRow.publication_year,
            BookRow.series,
            BookRow.volume,
            BookRow.plot,
            CollectionBookRow.copies,
            CollectionBookRow.is_read,
        )
        .join(BookRow, BookRow.id == CollectionBookRow.book_id)
        .where(owned)
        .order_by(BookRow.title, BookRow.id)
    ).all()
    authors: dict[UUID, list[str]] = {}
    for book_id, name, surname in session.execute(
        select(BookAuthorRow.book_id, AuthorRow.name, AuthorRow.surname)
        .select_from(CollectionBookRow)
        .join(BookAuthorRow, BookAuthorRow.book_id == CollectionBookRow.book_id)
        .join(AuthorRow, AuthorRow.id == BookAuthorRow.author_id)
        .where(owned)
        .order_by(BookAuthorRow.book_id, BookAuthorRow.sort_order)
    ):
        authors.setdefault(book_id, []).append(str(Author(name or "", surname)))
    categories: dict[UUID, list[str]] = {}
    for book_id, category in session.execute(
        select(BookCategoryRow.book_id, BookCategoryRow.category)
        .select_from(CollectionBookRow)
        .join(BookCategoryRow, BookCategoryRow.book_id == CollectionBookRow.book_id)
        .where(owned)
        .order_by(BookCategoryRow.book_id, BookCategoryRow.category)
    ):
        categories.setdefault(book_id, []).append(category)

    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for (
        book_id,
        title,
        isbn,
        publisher,
        year,
        series,
        volume,
        plot,
        copies,
        read,
    ) in books:
        values = (
            title,
            f"{_SEPARATOR} ".join(authors.get(book_id, [])),
            isbn or "",
            publisher or "",
            "" if year is None else str(year),
            series or "",
            volume or "",
            plot or "",
            f"{_SEPARATOR} ".join(categories.get(book_id, [])),
            str(copies),
            "yes" if read else "no",
        )
        writer.writerow([_guard(v) for v in values])
    return out.getvalue()


def _text(row: dict[str, str], column: str) -> str | None:
    """A column's trimmed text, None when blank; _RowError when too long."""
    value = _unguard((row.get(column) or "").strip()) or None
    max_length = _MAX_LENGTHS.get(column)
    if value and max_length and len(value) > max_length:
        raise _RowError(f"the {column} cannot be longer than {max_length} characters.")
    return value


def _number(text: str | None, low: int, high: int) -> int | None:
    """`text` as a whole number from low to high; None if it is not one."""
    if text is None or not text.isdigit() or not low <= int(text) <= high:
        return None
    return int(text)


def _year(text: str | None) -> int | None:
    """
    The publication year: a whole number, or the year of a date (its only
    four-digit number); _RowError when there is no single year in it.
    """
    if text is None:
        return None
    years = [text] if text.isdigit() else _DATE_YEAR.findall(text)
    year = _number(years[0], 1, 9999) if len(years) == 1 else None
    if year is None:
        raise _RowError(
            "the year must be a whole number from 1 to 9999, or a date with it."
        )
    return year


def _authors(text: str | None) -> tuple[Author, ...]:
    """
    "Name Surname" authors separated by commas: the last word is the surname
    ("Homer" alone has only one), so "Ursula K. Le Guin" gives "Guin".
    """
    authors = []
    for part in (text or "").split(_SEPARATOR):
        words = part.split()
        if not words:
            continue
        author = Author(" ".join(words[:-1]), words[-1])
        if max(len(author.name), len(author.surname)) > _AUTHOR_MAX_LENGTH:
            raise _RowError(
                f"author names cannot be longer than {_AUTHOR_MAX_LENGTH} characters."
            )
        authors.append(author)
    return tuple(unique_authors(tuple(authors)))


def _categories(text: str | None) -> tuple[str, ...]:
    categories = unique_categories(tuple((text or "").split(_SEPARATOR)))
    if any(len(category) > CATEGORY_MAX_LENGTH for category in categories):
        raise _RowError(
            f"categories cannot be longer than {CATEGORY_MAX_LENGTH} characters."
        )
    return tuple(categories)


def _isbn(text: str | None) -> str | None:
    """
    The ISBN-13 of an ISBN cell, tolerating what spreadsheets and copying do
    to it (see normalise_isbn). _RowError when it is not a valid ISBN, or
    was written in scientific notation.
    """
    if text is None:
        return None
    try:
        return normalise_isbn(text)
    except IsbnLostDigitsError as e:
        raise _RowError(
            f"the ISBN became a number in a spreadsheet ({text}), which lost its "
            "digits: format the column as text and enter the ISBN again."
        ) from e
    except InvalidIsbnError as e:
        raise _RowError(f"not a valid ISBN ({text}).") from e


def _parse_row(row: dict[str, str]) -> _Row:
    """A CSV row as a book with the user's copies and read; _RowError if invalid."""
    title = _text(row, "title")
    if title is None:
        raise _RowError("the title is required.")
    isbn = _isbn(_text(row, "isbn"))
    year = _year(_text(row, "year"))
    copies = _number(_text(row, "copies") or "1", 1, _MAX_COPIES)
    if copies is None:
        raise _RowError(f"copies must be a whole number from 1 to {_MAX_COPIES}.")
    read = (_text(row, "read") or "").casefold()
    if read not in _YES | _NO:
        raise _RowError("read must be yes or no.")
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
    return _Row(book, copies, read in _YES)


type _Rows = list[tuple[int, dict[str, str]]]


def _read_rows(text: str) -> tuple[_Rows, list[str]]:
    """
    The rows of a CSV text, numbered as in a spreadsheet, with only the
    COLUMNS, and the other columns, ignored (blank ones not even named),
    after checking its header and size; CsvFileError for a file that cannot
    be imported.
    """
    text = text.removeprefix("﻿")
    if not text.strip():
        raise CsvFileError("The file is empty.")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        named = [column.strip() for column in next(reader)]
        rows = list(reader)
    except csv.Error as e:
        raise CsvFileError(f"The file is not valid CSV: {e}.") from e
    header = [column.casefold() for column in named]
    ignored = [n for n, c in zip(named, header, strict=True) if n and c not in COLUMNS]
    repeated = sorted({c for c in header if c in COLUMNS and header.count(c) > 1})
    if repeated:
        raise CsvFileError(f"Column {', '.join(repeated)} is repeated.")
    if "title" not in header:
        raise CsvFileError("The file has no title column.")
    books = [(n, r) for n, r in enumerate(rows, start=2) if any(v.strip() for v in r)]
    if len(books) > MAX_ROWS:
        raise CsvFileError(
            f"The file has more than {MAX_ROWS} books: split it into smaller files."
        )
    known = [
        (number, {c: v for c, v in zip(header, values, strict=False) if c in COLUMNS})
        for number, values in books
    ]
    return known, ignored


def _same_authors(book: Book, authors: tuple[Author, ...]) -> bool:
    """
    Whether both have the same authors, by full name ("Name Surname",
    ignoring case and spaces between words): an exported "Ursula K. Le Guin"
    still matches Le Guin, though the import would split her name at "Guin".
    """

    def keys(found: tuple[Author, ...]) -> set[str]:
        return {" ".join(str(a).split()).casefold() for a in found}

    return keys(book.authors) == keys(authors)


def _find_by_title(session: Session, user_id: UUID, book: NewBook) -> Book | None:
    """The oldest catalogue book with the book's title and authors, ignoring case."""
    # Books with this title (ignoring case: UNICODE_CI), oldest first.
    ids = session.scalars(
        select(BookRow.id)
        .where(BookRow.title == book.title)
        .order_by(BookRow.created_at, BookRow.id)
    ).all()
    for book_id in ids:
        found = find_book(session, user_id, book_id)
        if found is not None and _same_authors(found, book.authors):
            return found
    return None


def _import_row(
    session: Session, user_id: UUID, row: _Row, report: ImportReport
) -> None:
    """
    Add the row's book to the user's collection: the catalogue book with its
    ISBN or, without one, with its title and authors; a new one if none.
    """
    book = row.book
    if book.isbn:
        found = find_book_by_isbn(session, user_id, book.isbn)
    else:
        found = _find_by_title(session, user_id, book)
    if found is None:
        add_book(session, user_id, book, row.copies, read=row.read)
        report.added += 1
        report.created += 1
        return
    if found.copies is None and add_to_collection(
        session, user_id, found.id, row.copies, read=row.read
    ):
        report.added += 1
        return
    report.skipped += 1


def import_collection(session: Session, user_id: UUID, text: str) -> ImportReport:
    """
    Add the books of a CSV text (see COLUMNS) to the user's collection, each
    row on its own: a bad row is reported and the rest still imported. A
    book already in the collection is skipped, unchanged. CsvFileError, with
    nothing imported, for a file without a usable header or with more than
    MAX_ROWS books. Columns not in COLUMNS are ignored (and reported).
    """
    rows, ignored = _read_rows(text)
    report = ImportReport(ignored_columns=ignored)
    for number, values in rows:
        try:
            _import_row(session, user_id, _parse_row(values), report)
        except (_RowError, BookExistsError, UnknownBookError) as e:
            # The last two: another user added or deleted the book meanwhile.
            report.errors.append(f"Row {number}: {e}")
        # A new snapshot: the next row's lookups see the books add_book just
        # committed, and so does the caller.
        session.commit()
    return report
