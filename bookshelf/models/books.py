import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from firebird.driver import Connection

__all__ = [
    "PAGE_SIZE",
    "SEARCH_MAX_LENGTH",
    "Book",
    "BookPage",
    "Sort",
    "list_books",
]

PAGE_SIZE = 50
# The longest searched column (TITLE, AUTHORS.NAME); a longer text matches nothing.
SEARCH_MAX_LENGTH = 255


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
