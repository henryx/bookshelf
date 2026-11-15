import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import UUID, uuid4

from firebird.driver import (
    Connection,
    Cursor,
    DatabaseError,
    DefaultAction,
    Isolation,
    tpb,
)

__all__ = [
    "CATEGORY_MAX_LENGTH",
    "PAGE_SIZE",
    "SEARCH_MAX_LENGTH",
    "SUGGESTIONS_LIMIT",
    "Author",
    "AuthorField",
    "Book",
    "BookDetails",
    "BookExistsError",
    "BookInCollectionError",
    "BookPage",
    "DeletedBook",
    "InvalidIsbnError",
    "IsbnAlreadySetError",
    "NewBook",
    "Sort",
    "UnknownBookError",
    "add_book",
    "add_to_collection",
    "delete_book",
    "find_book",
    "find_book_by_isbn",
    "find_book_details",
    "list_books",
    "list_collection",
    "normalise_isbn",
    "remove_from_collection",
    "set_copies",
    "set_read",
    "suggest_authors",
    "suggest_categories",
    "suggest_publishers",
    "unique_authors",
    "unique_categories",
    "update_book",
]

logger = logging.getLogger(__name__)

# Firebird GDS code for "violation of PRIMARY or UNIQUE KEY constraint".
_UNIQUE_KEY_VIOLATION = 335544665
# Firebird GDS code for "violation of FOREIGN KEY constraint".
_FOREIGN_KEY_VIOLATION = 335544466

PAGE_SIZE = 50
SUGGESTIONS_LIMIT = 10
# AUTHORS.NAME and SURNAME; a longer text matches no author.
_AUTHOR_NAME_MAX_LENGTH = 127
# BOOKS.PUBLISHER; a longer text matches no publisher.
_PUBLISHER_MAX_LENGTH = 255
# BOOK_CATEGORIES.CATEGORY; a longer text matches no category.
CATEGORY_MAX_LENGTH = 64
# The longest searched text (TITLE, or an author's name and surname); a
# longer text matches nothing.
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


class BookInCollectionError(Exception):
    """A book in a user's collection cannot be deleted."""

    def __init__(self, title: str) -> None:
        super().__init__(
            f"“{title}” is in someone’s collection: it can be deleted only when it is "
            "in nobody’s collection."
        )
        self.title = title


class IsbnAlreadySetError(Exception):
    """A book's ISBN can be set only while it has none."""

    def __init__(self, book_id: UUID) -> None:
        super().__init__("This book already has an ISBN.")
        self.book_id = book_id


class UnknownBookError(Exception):
    """No book in the catalogue has this ID (it may have just been deleted)."""

    def __init__(self, book_id: UUID) -> None:
        super().__init__(f"No book {book_id} in the catalogue.")
        self.book_id = book_id


class AuthorField(StrEnum):
    """The author field whose start is matched by suggest_authors."""

    NAME = "name"
    SURNAME = "surname"


class Sort(StrEnum):
    """Orders of the book list."""

    TITLE = "title"
    AUTHOR = "author"
    ADDED = "added"


@dataclass(frozen=True)
class Author:
    """
    An author's given name ('' when there is none, as for "Homer") and
    surname. `id`: the author's record, when read from one (as in the book
    lists); it plays no part in comparing authors, which is by name.
    """

    name: str
    surname: str
    id: UUID | None = field(default=None, compare=False, kw_only=True)

    def __str__(self) -> str:
        return f"{self.name} {self.surname}".strip()


@dataclass(frozen=True)
class Book:
    """
    A book as shown in the book lists. copies: how many the current user
    owns, or None when the book is not in their collection; read: whether
    they have read it (False when it is not in their collection).
    """

    id: UUID
    title: str
    authors: tuple[Author, ...]
    publication_year: int | None
    copies: int | None = None
    read: bool = False


@dataclass(frozen=True)
class NewBook:
    """
    A book to add. Optional texts are None when not given; authors and categories
    are trimmed, blank ones skipped and repeats (ignoring case) dropped.
    """

    title: str
    authors: tuple[Author, ...] = ()
    isbn: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    series: str | None = None
    volume: str | None = None
    plot: str | None = None
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class BookDetails:
    """
    A catalogue book with every field; categories in alphabetical order.
    collections: how many users have it in their collection.
    """

    id: UUID
    title: str
    authors: tuple[Author, ...] = ()
    isbn: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    series: str | None = None
    volume: str | None = None
    plot: str | None = None
    categories: tuple[str, ...] = ()
    collections: int = 0


@dataclass(frozen=True)
class DeletedBook:
    """A book delete_book deleted, and the authors that went with it (no other book)."""

    title: str
    authors_deleted: tuple[UUID, ...]


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


# Search: title, any author's "name surname" or ISBN contains the text,
# ignoring case; the BOOK_MATCHES function (db/schema.py) holds the
# condition, with the parameters from _search_params. The list queries count
# the matches themselves: this one is only for a page past the last.
_COUNT_QUERY = """
    SELECT COUNT(*) FROM BOOKS b
    WHERE BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
"""

# One page of books, then a row per author (one with NULL for a book without
# authors), in the page's order and each book's author order, with the
# current user's copies (NULL when the book is not in their collection) and,
# last, the number of books matching the search on every page (TOTAL), so
# the one scan the search needs also counts them.
_LIST_QUERIES: dict[Sort, str] = {
    Sort.TITLE: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, cb.COPIES, cb.IS_READ, a.NAME, a.SURNAME, a.ID, p.TOTAL
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, COUNT(*) OVER () AS TOTAL
            FROM BOOKS b
            WHERE BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
            ORDER BY b.TITLE, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = p.ID AND cb.USER_ID = ?
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.TITLE, p.ID, ba.SORT_ORDER
    """,
    # By first author's surname, then name; books without authors last.
    Sort.AUTHOR: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, cb.COPIES, cb.IS_READ, a.NAME, a.SURNAME, a.ID, p.TOTAL
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, COUNT(*) OVER () AS TOTAL,
                   (SELECT FIRST 1 fa.SURNAME
                    FROM BOOK_AUTHORS fba JOIN AUTHORS fa ON fa.ID = fba.AUTHOR_ID
                    WHERE fba.BOOK_ID = b.ID ORDER BY fba.SORT_ORDER) AS FIRST_SURNAME,
                   (SELECT FIRST 1 fa.NAME
                    FROM BOOK_AUTHORS fba JOIN AUTHORS fa ON fa.ID = fba.AUTHOR_ID
                    WHERE fba.BOOK_ID = b.ID ORDER BY fba.SORT_ORDER) AS FIRST_NAME
            FROM BOOKS b
            WHERE BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
            ORDER BY FIRST_SURNAME NULLS LAST, FIRST_NAME, b.TITLE, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = p.ID AND cb.USER_ID = ?
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.FIRST_SURNAME NULLS LAST, p.FIRST_NAME, p.TITLE, p.ID, ba.SORT_ORDER
    """,
    # Newest first.
    Sort.ADDED: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, cb.COPIES, cb.IS_READ, a.NAME, a.SURNAME, a.ID, p.TOTAL
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, b.CREATED_AT,
                   COUNT(*) OVER () AS TOTAL
            FROM BOOKS b
            WHERE BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
            ORDER BY b.CREATED_AT DESC, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = p.ID AND cb.USER_ID = ?
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.CREATED_AT DESC, p.ID, ba.SORT_ORDER
    """,
}

# The same search as _COUNT_QUERY, in one user's collection; also only for a
# page past the last.
_COLLECTION_COUNT_QUERY = """
    SELECT COUNT(*) FROM BOOKS b
    JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = b.ID
    WHERE cb.USER_ID = ?
      AND BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
"""

# As _LIST_QUERIES, in one user's collection. "Date added" is the date the
# book entered the catalogue: collections do not record when a book joined.
_COLLECTION_QUERIES: dict[Sort, str] = {
    Sort.TITLE: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, p.COPIES, p.IS_READ, a.NAME, a.SURNAME, a.ID, p.TOTAL
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, cb.COPIES, cb.IS_READ,
                   COUNT(*) OVER () AS TOTAL
            FROM BOOKS b
            JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = b.ID
            WHERE cb.USER_ID = ?
              AND BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
            ORDER BY b.TITLE, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.TITLE, p.ID, ba.SORT_ORDER
    """,
    Sort.AUTHOR: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, p.COPIES, p.IS_READ, a.NAME, a.SURNAME, a.ID, p.TOTAL
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, cb.COPIES, cb.IS_READ,
                   COUNT(*) OVER () AS TOTAL,
                   (SELECT FIRST 1 fa.SURNAME
                    FROM BOOK_AUTHORS fba JOIN AUTHORS fa ON fa.ID = fba.AUTHOR_ID
                    WHERE fba.BOOK_ID = b.ID ORDER BY fba.SORT_ORDER) AS FIRST_SURNAME,
                   (SELECT FIRST 1 fa.NAME
                    FROM BOOK_AUTHORS fba JOIN AUTHORS fa ON fa.ID = fba.AUTHOR_ID
                    WHERE fba.BOOK_ID = b.ID ORDER BY fba.SORT_ORDER) AS FIRST_NAME
            FROM BOOKS b
            JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = b.ID
            WHERE cb.USER_ID = ?
              AND BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
            ORDER BY FIRST_SURNAME NULLS LAST, FIRST_NAME, b.TITLE, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.FIRST_SURNAME NULLS LAST, p.FIRST_NAME, p.TITLE, p.ID, ba.SORT_ORDER
    """,
    Sort.ADDED: """
        SELECT p.ID, p.TITLE, p.PUBLICATION_YEAR, p.COPIES, p.IS_READ, a.NAME, a.SURNAME, a.ID, p.TOTAL
        FROM (
            SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, b.CREATED_AT, cb.COPIES, cb.IS_READ,
                   COUNT(*) OVER () AS TOTAL
            FROM BOOKS b
            JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = b.ID
            WHERE cb.USER_ID = ?
              AND BOOK_MATCHES(b.ID, b.TITLE, b.ISBN, ?, ?, ?, ?)
            ORDER BY b.CREATED_AT DESC, b.ID
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        ) p
        LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = p.ID
        LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
        ORDER BY p.CREATED_AT DESC, p.ID, ba.SORT_ORDER
    """,
}

# One book, a row per author as in the lists, with the user's copies.
_FIND_BOOK_QUERY = """
    SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, cb.COPIES, cb.IS_READ, a.NAME, a.SURNAME, a.ID
    FROM BOOKS b
    LEFT JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = b.ID AND cb.USER_ID = ?
    LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = b.ID
    LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
    WHERE b.ID = ?
    ORDER BY ba.SORT_ORDER
"""

_FIND_BOOK_BY_ISBN_QUERY = """
    SELECT b.ID, b.TITLE, b.PUBLICATION_YEAR, cb.COPIES, cb.IS_READ, a.NAME, a.SURNAME, a.ID
    FROM BOOKS b
    LEFT JOIN COLLECTION_BOOKS cb ON cb.BOOK_ID = b.ID AND cb.USER_ID = ?
    LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = b.ID
    LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
    WHERE b.ISBN = ?
    ORDER BY ba.SORT_ORDER
"""

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


def _isbn_searches(text: str) -> tuple[str, str]:
    """
    The two texts to look for in ISBNs, which are stored as 13 digits. First
    the text as typed, with hyphens and spaces removed from digits (other text
    is kept as is: it has letters, so it matches no ISBN). Second, for a valid
    ISBN-10, its ISBN-13 without the check digit (the only part that differs);
    otherwise the first again. Both are needed: the first 10 digits of an
    ISBN-13 may also be a valid ISBN-10.
    """
    compact = _ISBN_SEPARATORS.sub("", text)
    typed = compact if compact.isdigit() else text
    if _ISBN_10.fullmatch(compact) and _isbn_10_valid(compact):
        return typed, f"978{compact[:9]}"
    return typed, typed


def _search_params(
    search: str, author_id: UUID | None
) -> tuple[str, str, str, bytes | None]:
    """BOOK_MATCHES's search parameters: the text, the ISBN texts, the author."""
    return search, *_isbn_searches(search), author_id.bytes if author_id else None


# ID, TITLE, PUBLICATION_YEAR, the user's COPIES and IS_READ, then one author's
# NAME, SURNAME, ID.
type _BookRow = tuple[
    bytes,
    str,
    int | None,
    int | None,
    bool | None,
    str | None,
    str | None,
    bytes | None,
]


def _books(rows: list[_BookRow]) -> list[Book]:
    """Group the per-author rows into books, keeping their order."""
    books: dict[bytes, Book] = {}
    for book_id, title, year, copies, read, name, surname, author_id in rows:
        book = books.get(book_id) or Book(
            UUID(bytes=book_id), title, (), year, copies, read=bool(read)
        )
        if surname is not None and author_id is not None:
            author = Author(name or "", surname, id=UUID(bytes=author_id))
            book = replace(book, authors=(*book.authors, author))
        books[book_id] = book
    return list(books.values())


type _Query = tuple[str, tuple[object, ...]]


def _book_page(
    cur: Cursor,
    list_query: Callable[[int], _Query],
    count_query: _Query,
    page: int,
    page_size: int,
) -> BookPage:
    """
    One page of books from `list_query` (the query and its parameters for an
    offset), whose rows end with the total. A page out of range gives the
    nearest existing page; only then is `count_query` run, to find it.
    """
    page = max(page, 1)
    cur.execute(*list_query((page - 1) * page_size))
    rows = cur.fetchall()
    if not rows and page > 1:
        cur.execute(*count_query)
        (total,) = cur.fetchone()
        page = BookPage([], total, 1, page_size).pages
        cur.execute(*list_query((page - 1) * page_size))
        rows = cur.fetchall()
    total = rows[0][-1] if rows else 0
    return BookPage(_books([row[:-1] for row in rows]), total, page, page_size)


def list_books(
    conn: Connection,
    user_id: UUID,
    search: str = "",
    sort: Sort = Sort.TITLE,
    page: int = 1,
    *,
    author_id: UUID | None = None,
    page_size: int = PAGE_SIZE,
) -> BookPage:
    """
    One page of the catalogue, filtered by `search` (title, author or ISBN,
    ignoring case) and, when given, to the books of the author `author_id`,
    with the copies `user_id` owns of each book. A page out of range gives
    the nearest existing page.
    """
    search = search.strip()
    if len(search) > SEARCH_MAX_LENGTH:
        return BookPage([], 0, 1, page_size)
    params = _search_params(search, author_id)
    with conn.cursor() as cur:
        return _book_page(
            cur,
            lambda offset: (
                _LIST_QUERIES[sort],
                (*params, offset, page_size, user_id.bytes),
            ),
            (_COUNT_QUERY, params),
            page,
            page_size,
        )


def list_collection(
    conn: Connection,
    user_id: UUID,
    search: str = "",
    sort: Sort = Sort.TITLE,
    page: int = 1,
    *,
    author_id: UUID | None = None,
    page_size: int = PAGE_SIZE,
) -> BookPage:
    """One page of the books in `user_id`'s collection, as list_books."""
    search = search.strip()
    if len(search) > SEARCH_MAX_LENGTH:
        return BookPage([], 0, 1, page_size)
    params = (user_id.bytes, *_search_params(search, author_id))
    with conn.cursor() as cur:
        return _book_page(
            cur,
            lambda offset: (_COLLECTION_QUERIES[sort], (*params, offset, page_size)),
            (_COLLECTION_COUNT_QUERY, params),
            page,
            page_size,
        )


def find_book(conn: Connection, user_id: UUID, book_id: UUID) -> Book | None:
    """The catalogue book `book_id`, with the copies `user_id` owns; None if unknown."""
    with conn.cursor() as cur:
        cur.execute(_FIND_BOOK_QUERY, (user_id.bytes, book_id.bytes))
        books = _books(cur.fetchall())
    return books[0] if books else None


def find_book_details(conn: Connection, book_id: UUID) -> BookDetails | None:
    """Every field of the catalogue book `book_id`; None if unknown."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT b.TITLE, b.ISBN, b.PUBLISHER, b.PUBLICATION_YEAR, b.SERIES, "
            "b.VOLUME, b.PLOT, "
            "(SELECT COUNT(*) FROM COLLECTION_BOOKS cb WHERE cb.BOOK_ID = b.ID) "
            "FROM BOOKS b WHERE b.ID = ?",
            (book_id.bytes,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        title, isbn, publisher, year, series, volume, plot, collections = row
        cur.execute(
            "SELECT a.NAME, a.SURNAME FROM BOOK_AUTHORS ba "
            "JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID "
            "WHERE ba.BOOK_ID = ? ORDER BY ba.SORT_ORDER",
            (book_id.bytes,),
        )
        authors = tuple(Author(name, surname) for name, surname in cur.fetchall())
        cur.execute(
            "SELECT CATEGORY FROM BOOK_CATEGORIES WHERE BOOK_ID = ? ORDER BY CATEGORY",
            (book_id.bytes,),
        )
        categories = tuple(category for (category,) in cur.fetchall())
    return BookDetails(
        id=book_id,
        title=title,
        authors=authors,
        isbn=isbn,
        publisher=publisher,
        publication_year=year,
        series=series,
        volume=volume,
        plot=plot,
        categories=categories,
        collections=collections,
    )


def find_book_by_isbn(conn: Connection, user_id: UUID, isbn: str) -> Book | None:
    """The catalogue book with this ISBN-13, as find_book; None if there is none."""
    with conn.cursor() as cur:
        cur.execute(_FIND_BOOK_BY_ISBN_QUERY, (user_id.bytes, isbn))
        books = _books(cur.fetchall())
    return books[0] if books else None


def add_to_collection(
    conn: Connection,
    user_id: UUID,
    book_id: UUID,
    copies: int = 1,
    *,
    read: bool = False,
) -> bool:
    """
    Add a catalogue book to the user's collection with `copies` copies,
    marked as `read` or not.
    False, changing nothing, when it is already there; UnknownBookError when
    the catalogue has no such book.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO COLLECTION_BOOKS (USER_ID, BOOK_ID, COPIES, IS_READ) "
                "VALUES (?, ?, ?, ?)",
                (user_id.bytes, book_id.bytes, copies, read),
            )
    except DatabaseError as e:
        if _FOREIGN_KEY_VIOLATION in e.gds_codes:
            raise UnknownBookError(book_id) from e
        if _UNIQUE_KEY_VIOLATION not in e.gds_codes:
            raise
        return False
    conn.commit()
    return True


def set_copies(conn: Connection, user_id: UUID, book_id: UUID, copies: int) -> bool:
    """Set how many copies of a book the user owns; False if it is not in their collection."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE COLLECTION_BOOKS SET COPIES = ? WHERE USER_ID = ? AND BOOK_ID = ?",
            (copies, user_id.bytes, book_id.bytes),
        )
        updated = cur.affected_rows > 0
    conn.commit()
    return updated


def set_read(conn: Connection, user_id: UUID, book_id: UUID, *, read: bool) -> bool:
    """Mark a book in the user's collection as read or not; False if it is not there."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE COLLECTION_BOOKS SET IS_READ = ? WHERE USER_ID = ? AND BOOK_ID = ?",
            (read, user_id.bytes, book_id.bytes),
        )
        updated = cur.affected_rows > 0
    conn.commit()
    return updated


def remove_from_collection(conn: Connection, user_id: UUID, book_id: UUID) -> bool:
    """
    Remove a book from the user's collection, leaving it in the catalogue.
    False if it was not there.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM COLLECTION_BOOKS WHERE USER_ID = ? AND BOOK_ID = ?",
            (user_id.bytes, book_id.bytes),
        )
        removed = cur.affected_rows > 0
    conn.commit()
    return removed


def unique_authors(authors: tuple[Author, ...]) -> list[Author]:
    """
    Trimmed authors with a surname, the first of each kept (ignoring case).
    Rows left entirely empty are skipped.
    """
    seen: set[tuple[str, str]] = set()
    unique = []
    for author in authors:
        trimmed = Author(author.name.strip(), author.surname.strip())
        key = (trimmed.name.casefold(), trimmed.surname.casefold())
        if (trimmed.name or trimmed.surname) and key not in seen:
            seen.add(key)
            unique.append(trimmed)
    return unique


def unique_categories(names: tuple[str, ...]) -> list[str]:
    """Trimmed, non-blank categories, the first of each kept (ignoring case)."""
    seen: set[str] = set()
    unique = []
    for name in (n.strip() for n in names):
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            unique.append(name)
    return unique


def _find_author(cur: Cursor, author: Author) -> bytes | None:
    # Both ignore case (UNICODE_CI), so "TERRY PRATCHETT" finds "Terry Pratchett".
    cur.execute(
        "SELECT ID FROM AUTHORS WHERE SURNAME = ? AND NAME = ?",
        (author.surname, author.name),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _author_id(cur: Cursor, author: Author) -> bytes:
    """The existing author with this name and surname (ignoring case), or a new one."""
    author_id = _find_author(cur, author)
    if author_id is not None:
        return author_id
    author_id = uuid4().bytes
    try:
        cur.execute(
            "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
            (author_id, author.name, author.surname),
        )
    except DatabaseError as e:
        # Committed by someone else since the lookup: use theirs. The
        # transaction is read committed (see add_book), so it sees that row.
        existing = _find_author(cur, author)
        if _UNIQUE_KEY_VIOLATION not in e.gds_codes or existing is None:
            raise
        return existing
    return author_id


def _insert_authors(cur: Cursor, book_id: UUID, authors: tuple[Author, ...]) -> None:
    """Link a book (with no authors yet) to these authors, in order: see unique_authors."""
    for order, author in enumerate(unique_authors(authors), start=1):
        cur.execute(
            "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) "
            "VALUES (?, ?, ?)",
            (book_id.bytes, _author_id(cur, author), order),
        )


def _replace_authors(cur: Cursor, book_id: UUID, authors: tuple[Author, ...]) -> None:
    """
    Make these the book's authors, in order. A former author that no book
    uses any more is deleted, so a misspelled name stops being suggested.
    """
    former = _author_ids(cur, book_id)
    # All at once: the rows are checked one by one, so they cannot be reordered in place.
    cur.execute("DELETE FROM BOOK_AUTHORS WHERE BOOK_ID = ?", (book_id.bytes,))
    _insert_authors(cur, book_id, authors)
    _delete_unused_authors(cur, former)


def _author_ids(cur: Cursor, book_id: UUID) -> list[bytes]:
    cur.execute(
        "SELECT AUTHOR_ID FROM BOOK_AUTHORS WHERE BOOK_ID = ?", (book_id.bytes,)
    )
    return [author_id for (author_id,) in cur.fetchall()]


def _delete_unused_authors(cur: Cursor, author_ids: list[bytes]) -> list[UUID]:
    """Delete each of these authors that no book uses any more; the ones deleted."""
    deleted = []
    for author_id in author_ids:
        try:
            cur.execute(
                "DELETE FROM AUTHORS a WHERE a.ID = ? AND NOT EXISTS ("
                "SELECT 1 FROM BOOK_AUTHORS ba WHERE ba.AUTHOR_ID = a.ID)",
                (author_id,),
            )
        except DatabaseError as e:
            # A book being added at this moment uses it: keep it.
            if _FOREIGN_KEY_VIOLATION not in e.gds_codes:
                raise
            logger.info(
                "Author %s kept: a book being saved at this moment uses it",
                UUID(bytes=author_id),
            )
            continue
        if cur.affected_rows > 0:
            deleted.append(UUID(bytes=author_id))
    return deleted


def _insert_book(
    cur: Cursor,
    user_id: UUID,
    book_id: UUID,
    book: NewBook,
    isbn: str | None,
    copies: int,
    read: bool,
) -> None:
    try:
        cur.execute(
            "INSERT INTO BOOKS (ID, TITLE, ISBN, PUBLISHER, PUBLICATION_YEAR, "
            "SERIES, VOLUME, PLOT) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book_id.bytes,
                book.title.strip(),
                isbn,
                book.publisher,
                book.publication_year,
                book.series,
                book.volume,
                book.plot,
            ),
        )
    except DatabaseError as e:
        # The ID is new, so the only key that can clash is the ISBN.
        if isbn is not None and _UNIQUE_KEY_VIOLATION in e.gds_codes:
            raise BookExistsError(isbn) from e
        raise
    _insert_authors(cur, book_id, book.authors)
    for category in unique_categories(book.categories):
        cur.execute(
            "INSERT INTO BOOK_CATEGORIES (BOOK_ID, CATEGORY) VALUES (?, ?)",
            (book_id.bytes, category),
        )
    cur.execute(
        "INSERT INTO COLLECTION_BOOKS (USER_ID, BOOK_ID, COPIES, IS_READ) "
        "VALUES (?, ?, ?, ?)",
        (user_id.bytes, book_id.bytes, copies, read),
    )


def add_book(
    conn: Connection,
    user_id: UUID,
    book: NewBook,
    copies: int = 1,
    *,
    read: bool = False,
) -> UUID:
    """
    Add a book to the catalogue and to the user's collection with `copies`
    copies, marked as `read` or not, in one transaction. Authors are picked by name and surname, or
    created; one with a name but no surname is refused by the database.
    Raises InvalidIsbnError for a bad ISBN and BookExistsError if the ISBN
    is already in the catalogue.

    The transaction is its own, read committed, and leaves the connection's
    main one alone. A snapshot transaction could not use an author committed
    by someone else after it started: neither its lookup nor the foreign key
    check on BOOK_AUTHORS would see that author, though its insert clashes.
    """
    isbn = normalise_isbn(book.isbn) if book.isbn else None
    book_id = uuid4()
    with (
        conn.transaction_manager(
            tpb(Isolation.READ_COMMITTED_RECORD_VERSION),
            default_action=DefaultAction.ROLLBACK,
        ) as transaction,
        transaction.cursor() as cur,
    ):
        _insert_book(cur, user_id, book_id, book, isbn, copies, read)
        transaction.commit()
    return book_id


def _set_isbn(cur: Cursor, book_id: UUID, isbn: str) -> None:
    """
    Give a book without an ISBN this one. The ISBN IS NULL condition keeps
    an ISBN from changing, even when the caller's view of the book is out of
    date: only _replace_isbn (admins) changes one.
    """
    try:
        cur.execute(
            "UPDATE BOOKS SET ISBN = ? WHERE ID = ? AND ISBN IS NULL",
            (isbn, book_id.bytes),
        )
    except DatabaseError as e:
        if _UNIQUE_KEY_VIOLATION in e.gds_codes:
            raise BookExistsError(isbn) from e
        raise
    if cur.affected_rows == 0:
        raise IsbnAlreadySetError(book_id)


def _replace_isbn(cur: Cursor, book_id: UUID, isbn: str | None) -> None:
    """Set a book's ISBN, whatever it was, or remove it (None)."""
    try:
        cur.execute("UPDATE BOOKS SET ISBN = ? WHERE ID = ?", (isbn, book_id.bytes))
    except DatabaseError as e:
        if isbn is not None and _UNIQUE_KEY_VIOLATION in e.gds_codes:
            raise BookExistsError(isbn) from e
        raise


def update_book(
    conn: Connection,
    book_id: UUID,
    *,
    authors: tuple[Author, ...],
    publisher: str | None,
    publication_year: int | None,
    series: str | None,
    volume: str | None,
    plot: str | None,
    categories: tuple[str, ...],
    isbn: str | None = None,
    replace_isbn: bool = False,
) -> None:
    """
    Save a book's publisher, year, series, volume and plot (None empties them) and
    replace its authors and categories (picked or created as when adding a book),
    in one transaction; the title never changes, and an author no book uses
    any more is deleted. With `isbn`, also give the book that ISBN, stored
    as ISBN-13, if it has none. With `replace_isbn` (admins correcting a
    wrong one), the ISBN becomes `isbn` whatever it was, and None removes it.
    Nothing is saved when it raises: UnknownBookError for an unknown book,
    InvalidIsbnError for a bad ISBN, BookExistsError if another book has it,
    IsbnAlreadySetError if this book already has one (without replace_isbn).

    The transaction is its own and read committed, as in add_book.
    """
    new_isbn = normalise_isbn(isbn) if isbn else None
    with (
        conn.transaction_manager(
            tpb(Isolation.READ_COMMITTED_RECORD_VERSION),
            default_action=DefaultAction.ROLLBACK,
        ) as transaction,
        transaction.cursor() as cur,
    ):
        cur.execute(
            "UPDATE BOOKS SET PUBLISHER = ?, PUBLICATION_YEAR = ?, SERIES = ?, "
            "VOLUME = ?, PLOT = ? WHERE ID = ?",
            (publisher, publication_year, series, volume, plot, book_id.bytes),
        )
        if cur.affected_rows == 0:
            raise UnknownBookError(book_id)
        _replace_authors(cur, book_id, authors)
        cur.execute("DELETE FROM BOOK_CATEGORIES WHERE BOOK_ID = ?", (book_id.bytes,))
        for category in unique_categories(categories):
            cur.execute(
                "INSERT INTO BOOK_CATEGORIES (BOOK_ID, CATEGORY) VALUES (?, ?)",
                (book_id.bytes, category),
            )
        if replace_isbn:
            _replace_isbn(cur, book_id, new_isbn)
        elif new_isbn is not None:
            _set_isbn(cur, book_id, new_isbn)
        transaction.commit()


def delete_book(conn: Connection, book_id: UUID) -> DeletedBook:
    """
    Delete a book for good, with its author links and categories. An author it
    leaves without books is deleted too, as when editing.
    Raises UnknownBookError for an unknown book and BookInCollectionError if
    it is in any user's collection (even one added at this moment); nothing
    changes then.

    The transaction is its own and read committed, as in update_book.
    """
    with (
        conn.transaction_manager(
            tpb(Isolation.READ_COMMITTED_RECORD_VERSION),
            default_action=DefaultAction.ROLLBACK,
        ) as transaction,
        transaction.cursor() as cur,
    ):
        cur.execute("SELECT TITLE FROM BOOKS WHERE ID = ?", (book_id.bytes,))
        row = cur.fetchone()
        if row is None:
            raise UnknownBookError(book_id)
        (title,) = row
        authors = _author_ids(cur, book_id)
        try:
            # Its BOOK_AUTHORS and BOOK_CATEGORIES rows go with it (ON DELETE CASCADE).
            cur.execute("DELETE FROM BOOKS WHERE ID = ?", (book_id.bytes,))
        except DatabaseError as e:
            # FK_COLLECTION_BOOKS_BOOK: someone owns it.
            if _FOREIGN_KEY_VIOLATION in e.gds_codes:
                raise BookInCollectionError(title) from e
            raise
        if cur.affected_rows == 0:
            raise UnknownBookError(book_id)
        deleted = _delete_unused_authors(cur, authors)
        transaction.commit()
    return DeletedBook(title, tuple(deleted))


def suggest_authors(conn: Connection, text: str, field: AuthorField) -> list[Author]:
    """
    Up to SUGGESTIONS_LIMIT authors whose name (or surname) starts with
    `text`, ignoring case. Blank or too long text suggests nothing.
    """
    text = text.strip()
    if not text or len(text) > _AUTHOR_NAME_MAX_LENGTH:
        return []
    # STARTING WITH follows the columns' UNICODE_CI collation: case is ignored.
    match field:
        case AuthorField.NAME:
            query = (
                "SELECT NAME, SURNAME FROM AUTHORS WHERE NAME STARTING WITH ? "
                "ORDER BY NAME, SURNAME FETCH FIRST ? ROWS ONLY"
            )
        case AuthorField.SURNAME:
            query = (
                "SELECT NAME, SURNAME FROM AUTHORS WHERE SURNAME STARTING WITH ? "
                "ORDER BY SURNAME, NAME FETCH FIRST ? ROWS ONLY"
            )
    with conn.cursor() as cur:
        cur.execute(query, (text, SUGGESTIONS_LIMIT))
        return [Author(name, surname) for name, surname in cur.fetchall()]


def suggest_publishers(conn: Connection, text: str) -> list[str]:
    """
    Up to SUGGESTIONS_LIMIT publishers used in the catalogue that start with
    `text`, ignoring case; spellings differing only in case are one
    publisher. Blank or too long text suggests nothing.
    """
    text = text.strip()
    if not text or len(text) > _PUBLISHER_MAX_LENGTH:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT PUBLISHER FROM BOOKS WHERE PUBLISHER STARTING WITH ? "
            "ORDER BY PUBLISHER FETCH FIRST ? ROWS ONLY",
            (text, SUGGESTIONS_LIMIT),
        )
        return [publisher for (publisher,) in cur.fetchall()]


def suggest_categories(conn: Connection, text: str) -> list[str]:
    """
    Up to SUGGESTIONS_LIMIT categories used in the catalogue that start with
    `text`, ignoring case; spellings differing only in case are one
    category. Blank or too long text suggests nothing.
    """
    text = text.strip()
    if not text or len(text) > CATEGORY_MAX_LENGTH:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT CATEGORY FROM BOOK_CATEGORIES "
            "WHERE CATEGORY STARTING WITH ? "
            "ORDER BY CATEGORY FETCH FIRST ? ROWS ONLY",
            (text, SUGGESTIONS_LIMIT),
        )
        return [category for (category,) in cur.fetchall()]
