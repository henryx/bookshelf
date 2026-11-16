"""The catalogue and collection lists, and finding one book."""

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from firebird.driver import Connection, Cursor

from bookshelf.models.books.isbn import isbn_searches
from bookshelf.models.books.model import (
    PAGE_SIZE,
    SEARCH_MAX_LENGTH,
    Author,
    Book,
    BookDetails,
    BookPage,
    Query,
    Sort,
)

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


def _search_params(
    search: str, author_id: UUID | None
) -> tuple[str, str, str, bytes | None]:
    """BOOK_MATCHES's search parameters: the text, the ISBN texts, the author."""
    return search, *isbn_searches(search), author_id.bytes if author_id else None


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


def _book_page(
    cur: Cursor,
    list_query: Callable[[int], Query],
    count_query: Query,
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
