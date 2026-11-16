"""Suggestions for the book forms: books, authors, publishers, categories."""

from dataclasses import replace
from uuid import UUID

from firebird.driver import Connection

from bookshelf.models.books.isbn import isbn_searches
from bookshelf.models.books.model import (
    CATEGORY_MAX_LENGTH,
    Author,
    AuthorField,
    BookField,
    BookSuggestion,
    Query,
)

SUGGESTIONS_LIMIT = 10
# AUTHORS.NAME and SURNAME; a longer text matches no author.
_AUTHOR_NAME_MAX_LENGTH = 127
# BOOKS.PUBLISHER; a longer text matches no publisher.
_PUBLISHER_MAX_LENGTH = 255
# BOOKS.TITLE; a longer text matches no title.
_TITLE_MAX_LENGTH = 255

# Up to ? books whose title (or ISBN, either of two texts) starts with ?, then
# a row per author as in the lists (one with NULL for a book without authors).
_SUGGEST_BY_TITLE_QUERY = """
    SELECT s.ID, s.TITLE, s.ISBN, s.PUBLICATION_YEAR, a.NAME, a.SURNAME
    FROM (
        SELECT b.ID, b.TITLE, b.ISBN, b.PUBLICATION_YEAR FROM BOOKS b
        WHERE b.TITLE STARTING WITH ?
        ORDER BY b.TITLE, b.ID
        FETCH FIRST ? ROWS ONLY
    ) s
    LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = s.ID
    LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
    ORDER BY s.TITLE, s.ID, ba.SORT_ORDER
"""

_SUGGEST_BY_ISBN_QUERY = """
    SELECT s.ID, s.TITLE, s.ISBN, s.PUBLICATION_YEAR, a.NAME, a.SURNAME
    FROM (
        SELECT b.ID, b.TITLE, b.ISBN, b.PUBLICATION_YEAR FROM BOOKS b
        WHERE b.ISBN STARTING WITH ? OR b.ISBN STARTING WITH ?
        ORDER BY b.ISBN
        FETCH FIRST ? ROWS ONLY
    ) s
    LEFT JOIN BOOK_AUTHORS ba ON ba.BOOK_ID = s.ID
    LEFT JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
    ORDER BY s.ISBN, ba.SORT_ORDER
"""

# An ISBN typed with more than this many characters (hyphens and spaces
# included) is no ISBN: it suggests nothing.
_ISBN_TEXT_MAX_LENGTH = 32


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


# ID, TITLE, ISBN, PUBLICATION_YEAR, then one author's NAME and SURNAME.
type _SuggestionRow = tuple[bytes, str, str | None, int | None, str | None, str | None]


def _suggestions(rows: list[_SuggestionRow]) -> list[BookSuggestion]:
    """Group the per-author rows into books, keeping their order."""
    books: dict[bytes, BookSuggestion] = {}
    for book_id, title, isbn, year, name, surname in rows:
        book = books.get(book_id) or BookSuggestion(
            UUID(bytes=book_id), title, isbn, year
        )
        if surname is not None:
            book = replace(book, authors=(*book.authors, Author(name or "", surname)))
        books[book_id] = book
    return list(books.values())


def _isbn_suggestion_texts(text: str) -> tuple[str, str] | None:
    """
    The two ISBN starts to look for (see isbn_searches), only digits; None
    when the text cannot start an ISBN.
    """
    if len(text) > _ISBN_TEXT_MAX_LENGTH:
        return None
    texts = [t for t in isbn_searches(text) if t.isdigit()]
    if not texts:
        return None
    return texts[0], texts[-1]


def suggest_books(
    conn: Connection, text: str, field: BookField
) -> list[BookSuggestion]:
    """
    Up to SUGGESTIONS_LIMIT catalogue books whose title starts with `text`
    (ignoring case), or whose ISBN starts with it (hyphens and spaces
    ignored; a whole ISBN-10 matches its ISBN-13). Blank, too long or (for
    ISBNs) non-digit text suggests nothing.
    """
    text = text.strip()
    if not text:
        return []
    match field:
        case BookField.TITLE:
            if len(text) > _TITLE_MAX_LENGTH:
                return []
            query: Query = (_SUGGEST_BY_TITLE_QUERY, (text, SUGGESTIONS_LIMIT))
        case BookField.ISBN:
            isbns = _isbn_suggestion_texts(text)
            if isbns is None:
                return []
            query = (_SUGGEST_BY_ISBN_QUERY, (*isbns, SUGGESTIONS_LIMIT))
    with conn.cursor() as cur:
        cur.execute(*query)
        return _suggestions(cur.fetchall())
