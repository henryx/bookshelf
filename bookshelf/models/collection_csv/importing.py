"""Importing a CSV file's books into a user's collection."""

import csv
import io
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from bookshelf.db.orm import BookRow
from bookshelf.models.books import (
    Author,
    Book,
    BookExistsError,
    NewBook,
    UnknownBookError,
    add_book,
    add_to_collection,
    find_book,
    find_book_by_isbn,
)
from bookshelf.models.collection_csv.columns import COLUMNS
from bookshelf.models.collection_csv.rows import Row, RowError, parse_row

__all__ = ["MAX_ROWS", "CsvFileError", "ImportReport", "import_collection"]

# Books one import may hold: each is a few queries and a commit, and the
# request has to end before the worker timeout.
MAX_ROWS = 1000


class CsvFileError(ValueError):
    """A file that cannot be imported at all (no rows were imported)."""


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
    session: Session, user_id: UUID, row: Row, report: ImportReport
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
        add_book(session, user_id, book, row.copies, read=row.read, score=row.score)
        report.added += 1
        report.created += 1
        return
    if found.copies is None and add_to_collection(
        session, user_id, found.id, row.copies, read=row.read, score=row.score
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
            _import_row(session, user_id, parse_row(values), report)
        except (RowError, BookExistsError, UnknownBookError) as e:
            # The last two: another user added or deleted the book meanwhile.
            report.errors.append(f"Row {number}: {e}")
        # A new snapshot: the next row's lookups see the books add_book just
        # committed, and so does the caller.
        session.commit()
    return report
