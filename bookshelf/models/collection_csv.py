"""A user's collection as CSV: exporting it, and importing books into it."""

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID

from firebird.driver import Connection

from bookshelf.models.books import (
    Author,
    Book,
    BookExistsError,
    InvalidIsbnError,
    NewBook,
    UnknownBookError,
    add_book,
    add_to_collection,
    find_book,
    find_book_by_isbn,
    normalise_isbn,
    unique_authors,
    unique_tags,
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
# order, but needs title. authors ("Name Surname", or "Surname" alone) and tags
# are separated by ", ", as the book form's tags. read: yes or no.
COLUMNS = (
    "title",
    "authors",
    "isbn",
    "publisher",
    "year",
    "series",
    "volume",
    "plot",
    "tags",
    "copies",
    "read",
)
# Books one import may hold: each is a few queries and a commit, and the
# request has to end before the worker timeout.
MAX_ROWS = 1000
_MAX_COPIES = 999
# Column sizes in BOOKS, AUTHORS and BOOK_TAGS.
_MAX_LENGTHS = {
    "title": 255,
    "publisher": 255,
    "series": 255,
    "volume": 32,
    "plot": 4000,
}
_AUTHOR_MAX_LENGTH = 127
_TAG_MAX_LENGTH = 64
# Between authors and between tags alike, as in the book form's tags.
_SEPARATOR = ","
_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0", ""}
# A spreadsheet runs a cell starting with one of these as a formula: the
# export puts a ' before it (shown as text), and the import takes it off.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


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
    spreadsheet (the header is row 1).
    """

    added: int = 0
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Row:
    book: NewBook
    copies: int
    read: bool


# Export: the user's books, then the authors and tags of all of them.
_EXPORT_BOOKS_QUERY = """
    SELECT b.ID, b.TITLE, b.ISBN, b.PUBLISHER, b.PUBLICATION_YEAR, b.SERIES,
        b.VOLUME, b.PLOT, cb.COPIES, cb.IS_READ
    FROM COLLECTION_BOOKS cb
    JOIN BOOKS b ON b.ID = cb.BOOK_ID
    WHERE cb.USER_ID = ?
    ORDER BY b.TITLE, b.ID
"""

_EXPORT_AUTHORS_QUERY = """
    SELECT ba.BOOK_ID, a.NAME, a.SURNAME
    FROM COLLECTION_BOOKS cb
    JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = cb.BOOK_ID
    JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
    WHERE cb.USER_ID = ?
    ORDER BY ba.BOOK_ID, ba.SORT_ORDER
"""

_EXPORT_TAGS_QUERY = """
    SELECT bt.BOOK_ID, bt.TAG
    FROM COLLECTION_BOOKS cb
    JOIN BOOK_TAGS bt ON bt.BOOK_ID = cb.BOOK_ID
    WHERE cb.USER_ID = ?
    ORDER BY bt.BOOK_ID, bt.TAG
"""

# Books with this title (ignoring case: UNICODE_CI), oldest first.
_BOOKS_BY_TITLE_QUERY = """
    SELECT ID FROM BOOKS WHERE TITLE = ? ORDER BY CREATED_AT, ID
"""


def _guard(text: str) -> str:
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def _unguard(text: str) -> str:
    return (
        text[1:] if text[:1] == "'" and text[1:].startswith(_FORMULA_STARTS) else text
    )


def export_collection(conn: Connection, user_id: UUID) -> str:
    """
    The user's collection as CSV text with COLUMNS, by title, starting with a
    byte order mark so that spreadsheets read it as UTF-8.
    """
    with conn.cursor() as cur:
        cur.execute(_EXPORT_BOOKS_QUERY, (user_id.bytes,))
        books = cur.fetchall()
        authors: dict[bytes, list[str]] = {}
        cur.execute(_EXPORT_AUTHORS_QUERY, (user_id.bytes,))
        for book_id, name, surname in cur.fetchall():
            author = str(Author(name or "", surname))
            authors.setdefault(book_id, []).append(author)
        tags: dict[bytes, list[str]] = {}
        cur.execute(_EXPORT_TAGS_QUERY, (user_id.bytes,))
        for book_id, tag in cur.fetchall():
            tags.setdefault(book_id, []).append(tag)

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
            f"{_SEPARATOR} ".join(tags.get(book_id, [])),
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


def _tags(text: str | None) -> tuple[str, ...]:
    tags = unique_tags(tuple((text or "").split(_SEPARATOR)))
    if any(len(tag) > _TAG_MAX_LENGTH for tag in tags):
        raise _RowError(f"tags cannot be longer than {_TAG_MAX_LENGTH} characters.")
    return tuple(tags)


def _isbn(text: str | None) -> str | None:
    if text is None:
        return None
    try:
        return normalise_isbn(text)
    except InvalidIsbnError as e:
        raise _RowError("not a valid ISBN.") from e


def _parse_row(row: dict[str, str]) -> _Row:
    """A CSV row as a book with the user's copies and read; _RowError if invalid."""
    title = _text(row, "title")
    if title is None:
        raise _RowError("the title is required.")
    isbn = _isbn(_text(row, "isbn"))
    year_text = _text(row, "year")
    year = _number(year_text, 1, 9999)
    if year_text is not None and year is None:
        raise _RowError("the year must be a whole number from 1 to 9999.")
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
        tags=_tags(_text(row, "tags")),
    )
    return _Row(book, copies, read in _YES)


def _read_rows(text: str) -> Iterator[tuple[int, dict[str, str]]]:
    """
    The rows of a CSV text, numbered as in a spreadsheet, after checking its
    header and size; CsvFileError for a file that cannot be imported.
    """
    text = text.removeprefix("﻿")
    if not text.strip():
        raise CsvFileError("The file is empty.")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [column.strip().casefold() for column in next(reader)]
        rows = list(reader)
    except csv.Error as e:
        raise CsvFileError(f"The file is not valid CSV: {e}.") from e
    unknown = [c for c in header if c not in COLUMNS]
    if unknown:
        raise CsvFileError(f"Unknown column: {', '.join(unknown)}.")
    repeated = sorted({c for c in header if header.count(c) > 1})
    if repeated:
        raise CsvFileError(f"Column {', '.join(repeated)} is repeated.")
    if "title" not in header:
        raise CsvFileError("The file has no title column.")
    books = [(n, r) for n, r in enumerate(rows, start=2) if any(v.strip() for v in r)]
    if len(books) > MAX_ROWS:
        raise CsvFileError(
            f"The file has more than {MAX_ROWS} books: split it into smaller files."
        )
    for number, values in books:
        yield number, dict(zip(header, values, strict=False))


def _same_authors(book: Book, authors: tuple[Author, ...]) -> bool:
    """
    Whether both have the same authors, by full name ("Name Surname",
    ignoring case and spaces between words): an exported "Ursula K. Le Guin"
    still matches Le Guin, though the import would split her name at "Guin".
    """

    def keys(found: tuple[Author, ...]) -> set[str]:
        return {" ".join(str(a).split()).casefold() for a in found}

    return keys(book.authors) == keys(authors)


def _find_by_title(conn: Connection, user_id: UUID, book: NewBook) -> Book | None:
    """The oldest catalogue book with the book's title and authors, ignoring case."""
    with conn.cursor() as cur:
        cur.execute(_BOOKS_BY_TITLE_QUERY, (book.title,))
        ids = [UUID(bytes=book_id) for (book_id,) in cur.fetchall()]
    for book_id in ids:
        found = find_book(conn, user_id, book_id)
        if found is not None and _same_authors(found, book.authors):
            return found
    return None


def _import_row(
    conn: Connection, user_id: UUID, row: _Row, report: ImportReport
) -> None:
    """
    Add the row's book to the user's collection: the catalogue book with its
    ISBN or, without one, with its title and authors; a new one if none.
    """
    book = row.book
    if book.isbn:
        found = find_book_by_isbn(conn, user_id, book.isbn)
    else:
        found = _find_by_title(conn, user_id, book)
    if found is None:
        add_book(conn, user_id, book, row.copies, read=row.read)
        report.added += 1
        report.created += 1
        return
    if found.copies is None and add_to_collection(
        conn, user_id, found.id, row.copies, read=row.read
    ):
        report.added += 1
        return
    report.skipped += 1


def import_collection(conn: Connection, user_id: UUID, text: str) -> ImportReport:
    """
    Add the books of a CSV text (see COLUMNS) to the user's collection, each
    row on its own: a bad row is reported and the rest still imported. A
    book already in the collection is skipped, unchanged. CsvFileError, with
    nothing imported, for a file without a usable header or with more than
    MAX_ROWS books.
    """
    report = ImportReport()
    for number, values in list(_read_rows(text)):
        try:
            _import_row(conn, user_id, _parse_row(values), report)
        except (_RowError, BookExistsError, UnknownBookError) as e:
            # The last two: another user added or deleted the book meanwhile.
            report.errors.append(f"Row {number}: {e}")
        # A new snapshot: the next row's lookups see the books add_book just
        # committed in its own transaction, and so does the caller.
        if conn.is_active():
            conn.commit()
    return report
