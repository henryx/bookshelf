import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from firebird.driver import Connection, Cursor, DatabaseError

__all__ = [
    "PAGE_SIZE",
    "SEARCH_MAX_LENGTH",
    "Book",
    "BookExistsError",
    "BookPage",
    "InvalidIsbnError",
    "NewBook",
    "Sort",
    "add_book",
    "list_books",
    "normalise_isbn",
]

# Firebird GDS code for "violation of PRIMARY or UNIQUE KEY constraint".
_UNIQUE_KEY_VIOLATION = 335544665

PAGE_SIZE = 50
# The longest searched column (TITLE, AUTHORS.NAME); a longer text matches nothing.
SEARCH_MAX_LENGTH = 255


class InvalidIsbnError(ValueError):
    """Text that is not a valid ISBN-10 or ISBN-13."""

    def __init__(self, isbn: str) -> None:
        super().__init__(f"Not a valid ISBN: {isbn}")
        self.isbn = isbn


class BookExistsError(Exception):
    """A book with the same ISBN is already in the catalogue."""

    def __init__(self, isbn: str) -> None:
        super().__init__(f"A book with ISBN {isbn} is already in the catalogue.")
        self.isbn = isbn


class Sort(StrEnum):
    """Orders of the book list."""

    TITLE = "title"
    AUTHOR = "author"
    ADDED = "added"


@dataclass(frozen=True)
class Book:
    """A book as shown in the book list."""

    id: UUID
    title: str
    authors: tuple[str, ...]
    publication_year: int | None


@dataclass(frozen=True)
class NewBook:
    """
    A book to add. Optional texts are None when not given; authors and tags
    are trimmed, blank ones skipped and repeats (ignoring case) dropped.
    """

    title: str
    authors: tuple[str, ...] = ()
    isbn: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    language: str | None = None
    notes: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class BookPage:
    books: list[Book]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        """Number of pages; at least 1, so an empty list still has a page."""
        return max(1, -(-self.total // self.page_size))


# Search: title, any author or ISBN contains the text, ignoring case.
# CONTAINING '' is true, so an empty search lists every book. The CASTs keep
# the parameters from taking the column types (CHAR(13) for ISBN).
_COUNT_QUERY = """
    SELECT COUNT(*) FROM BOOKS b
    WHERE b.TITLE CONTAINING CAST(? AS VARCHAR(255))
       OR b.ISBN CONTAINING CAST(? AS VARCHAR(255))
       OR EXISTS (
           SELECT 1 FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
           WHERE ba.BOOK_ID = b.ID AND a.NAME CONTAINING CAST(? AS VARCHAR(255))
       )
"""

# One page of books, then a row per author (one with NULL for a book without
# authors), in the page's order and each book's author order.
_LIST_QUERIES: dict[Sort, str] = {
    Sort.TITLE: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, a.NAME
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR FROM BOOKS b
            WHERE b.TITLE CONTAINING CAST(? AS VARCHAR(255))
               OR b.ISBN CONTAINING CAST(? AS VARCHAR(255))
               OR EXISTS (
                   SELECT 1 FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
                   WHERE ba.BOOK_ID = b.ID AND a.NAME CONTAINING CAST(? AS VARCHAR(255))
               )
            ORDER BY b.TITLE, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.TITLE, p.ID, ba.SORT_ORDER
    """,
    # By first author; books without authors last.
    Sort.AUTHOR: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, a.NAME
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR,
                   (SELECT FIRST 1 fa.NAME
                    FROM BOOK_AUTHORS fba JOIN AUTHORS fa ON fa.ID = fba.AUTHOR_ID
                    WHERE fba.BOOK_ID = b.ID ORDER BY fba.SORT_ORDER) AS FIRST_AUTHOR
            FROM BOOKS b
            WHERE b.TITLE CONTAINING CAST(? AS VARCHAR(255))
               OR b.ISBN CONTAINING CAST(? AS VARCHAR(255))
               OR EXISTS (
                   SELECT 1 FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
                   WHERE ba.BOOK_ID = b.ID AND a.NAME CONTAINING CAST(? AS VARCHAR(255))
               )
            ORDER BY FIRST_AUTHOR NULLS LAST, b.TITLE, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.FIRST_AUTHOR NULLS LAST, p.TITLE, p.ID, ba.SORT_ORDER
    """,
    # Newest first.
    Sort.ADDED: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, a.NAME
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, b.CREATED_AT FROM BOOKS b
            WHERE b.TITLE CONTAINING CAST(? AS VARCHAR(255))
               OR b.ISBN CONTAINING CAST(? AS VARCHAR(255))
               OR EXISTS (
                   SELECT 1 FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
                   WHERE ba.BOOK_ID = b.ID AND a.NAME CONTAINING CAST(? AS VARCHAR(255))
               )
            ORDER BY b.CREATED_AT DESC, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.CREATED_AT DESC, p.ID, ba.SORT_ORDER
    """,
}

_ISBN_SEPARATORS = re.compile(r"[\s-]")
_ISBN_10 = re.compile(r"\d{9}[\dXx]")
_ISBN_13 = re.compile(r"\d{13}")


def _isbn_13_check_digit(first_12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first_12))
    return str(-total % 10)


def _isbn_10_valid(isbn: str) -> bool:
    digits = [10 if d in "Xx" else int(d) for d in isbn]
    return sum(d * (10 - i) for i, d in enumerate(digits)) % 11 == 0


def normalise_isbn(text: str) -> str:
    """
    The 13-digit form of an ISBN-10 or ISBN-13, with hyphens and spaces
    removed. Raises InvalidIsbnError for anything else, a wrong check digit
    included.
    """
    compact = _ISBN_SEPARATORS.sub("", text)
    if _ISBN_13.fullmatch(compact):
        if _isbn_13_check_digit(compact[:12]) == compact[12]:
            return compact
    elif _ISBN_10.fullmatch(compact) and _isbn_10_valid(compact):
        first_12 = f"978{compact[:9]}"
        return first_12 + _isbn_13_check_digit(first_12)
    raise InvalidIsbnError(text)


def _isbn_search(text: str) -> str:
    """
    The text to look for in ISBNs, which are stored as 13 digits: hyphens and
    spaces removed, and an ISBN-10 turned into its ISBN-13 without the check
    digit (the only part that differs). Other text is kept as is: it has
    letters, so it matches no ISBN.
    """
    compact = _ISBN_SEPARATORS.sub("", text)
    if _ISBN_10.fullmatch(compact):
        return f"978{compact[:9]}"
    if compact.isdigit():
        return compact
    return text


def _search_params(search: str) -> tuple[str, str, str]:
    return search, _isbn_search(search), search


def _books(rows: list[tuple[bytes, str, int | None, str | None]]) -> list[Book]:
    """Group the per-author rows into books, keeping their order."""
    books: dict[bytes, Book] = {}
    for book_id, title, year, author in rows:
        book = books.get(book_id) or Book(UUID(bytes=book_id), title, (), year)
        if author is not None:
            book = Book(book.id, title, (*book.authors, author), year)
        books[book_id] = book
    return list(books.values())


def list_books(
    conn: Connection,
    search: str = "",
    sort: Sort = Sort.TITLE,
    page: int = 1,
    *,
    page_size: int = PAGE_SIZE,
) -> BookPage:
    """
    One page of the catalogue, filtered by `search` (title, author or ISBN,
    ignoring case). A page out of range gives the nearest existing page.
    """
    search = search.strip()
    if len(search) > SEARCH_MAX_LENGTH:
        return BookPage([], 0, 1, page_size)
    params = _search_params(search)
    with conn.cursor() as cur:
        cur.execute(_COUNT_QUERY, params)
        (total,) = cur.fetchone()
        result = BookPage([], total, 1, page_size)
        page = min(max(page, 1), result.pages)
        cur.execute(_LIST_QUERIES[sort], (*params, (page - 1) * page_size, page_size))
        return BookPage(_books(cur.fetchall()), total, page, page_size)


def _unique_ignoring_case(names: tuple[str, ...]) -> list[str]:
    """Trimmed, non-blank names, the first of each kept (ignoring case)."""
    seen: set[str] = set()
    unique = []
    for name in (n.strip() for n in names):
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            unique.append(name)
    return unique


def _find_author(cur: Cursor, name: str) -> bytes | None:
    # NAME ignores case (UNICODE_CI), so "TERRY PRATCHETT" finds "Terry Pratchett".
    cur.execute("SELECT ID FROM AUTHORS WHERE NAME = ?", (name,))
    row = cur.fetchone()
    return row[0] if row else None


def _author_id(cur: Cursor, name: str) -> bytes:
    """The existing author with this name (ignoring case), or a new one."""
    author_id = _find_author(cur, name)
    if author_id is not None:
        return author_id
    author_id = uuid4().bytes
    try:
        cur.execute("INSERT INTO AUTHORS (ID, NAME) VALUES (?, ?)", (author_id, name))
    except DatabaseError as e:
        # Added by someone else since the lookup: use theirs.
        existing = _find_author(cur, name)
        if _UNIQUE_KEY_VIOLATION not in e.gds_codes or existing is None:
            raise
        return existing
    return author_id


def add_book(conn: Connection, user_id: UUID, book: NewBook, copies: int = 1) -> UUID:
    """
    Add a book to the catalogue and to the user's collection with `copies`
    copies, in one transaction. Authors are picked by name or created.
    Raises InvalidIsbnError for a bad ISBN and BookExistsError if the ISBN
    is already in the catalogue.
    """
    isbn = normalise_isbn(book.isbn) if book.isbn else None
    book_id = uuid4()
    with conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO BOOKS (ID, TITLE, ISBN, PUBLISHER, PUBLICATION_YEAR, "
                "LANGUAGE, NOTES) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    book_id.bytes,
                    book.title.strip(),
                    isbn,
                    book.publisher,
                    book.publication_year,
                    book.language,
                    book.notes,
                ),
            )
        except DatabaseError as e:
            conn.rollback()
            # The ID is new, so the only key that can clash is the ISBN.
            if isbn is not None and _UNIQUE_KEY_VIOLATION in e.gds_codes:
                raise BookExistsError(isbn) from e
            raise
        for order, name in enumerate(_unique_ignoring_case(book.authors), start=1):
            cur.execute(
                "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) "
                "VALUES (?, ?, ?)",
                (book_id.bytes, _author_id(cur, name), order),
            )
        for tag in _unique_ignoring_case(book.tags):
            cur.execute(
                "INSERT INTO BOOK_TAGS (BOOK_ID, TAG) VALUES (?, ?)",
                (book_id.bytes, tag),
            )
        cur.execute(
            "INSERT INTO COLLECTION_BOOKS (USER_ID, BOOK_ID, COPIES) VALUES (?, ?, ?)",
            (user_id.bytes, book_id.bytes, copies),
        )
    conn.commit()
    return book_id
