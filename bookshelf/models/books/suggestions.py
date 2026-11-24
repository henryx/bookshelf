"""Suggestions for the book forms: books, authors, publishers, categories."""

from collections.abc import Iterable
from dataclasses import replace
from uuid import UUID

from sqlalchemy import ColumnElement, String, literal, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from bookshelf.db.orm import AuthorRow, BookAuthorRow, BookCategoryRow, BookRow
from bookshelf.models.books.isbn import isbn_searches
from bookshelf.models.books.model import (
    CATEGORY_MAX_LENGTH,
    Author,
    AuthorField,
    BookField,
    BookSuggestion,
)

SUGGESTIONS_LIMIT = 10
# AUTHORS.NAME and SURNAME; a longer text matches no author.
_AUTHOR_NAME_MAX_LENGTH = 127
# BOOKS.PUBLISHER; a longer text matches no publisher.
_PUBLISHER_MAX_LENGTH = 255
# BOOKS.TITLE; a longer text matches no title.
_TITLE_MAX_LENGTH = 255

# An ISBN typed with more than this many characters (hyphens and spaces
# included) is no ISBN: it suggests nothing.
_ISBN_TEXT_MAX_LENGTH = 32


def suggest_authors(session: Session, text: str, field: AuthorField) -> list[Author]:
    """
    Up to SUGGESTIONS_LIMIT authors whose name (or surname) starts with
    `text`, ignoring case. Blank or too long text suggests nothing.
    """
    text = text.strip()
    if not text or len(text) > _AUTHOR_NAME_MAX_LENGTH:
        return []
    match field:
        case AuthorField.NAME:
            first, second = AuthorRow.name, AuthorRow.surname
        case AuthorField.SURNAME:
            first, second = AuthorRow.surname, AuthorRow.name
    rows = session.execute(
        select(AuthorRow.name, AuthorRow.surname)
        .where(_starts_with(first, text, _AUTHOR_NAME_MAX_LENGTH))
        .order_by(first, second)
        .limit(SUGGESTIONS_LIMIT)
    )
    return [Author(name, surname) for name, surname in rows]


def _starts_with(
    column: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
    text: str,
    length: int,
) -> ColumnElement[bool]:
    """
    `column` starts with `text`; STARTING WITH follows the columns'
    UNICODE_CI collation, so case is ignored.
    """
    return column.op("STARTING WITH", is_comparison=True)(literal(text, String(length)))


def suggest_publishers(session: Session, text: str) -> list[str]:
    """
    Up to SUGGESTIONS_LIMIT publishers used in the catalogue that start with
    `text`, ignoring case; spellings differing only in case are one
    publisher. Blank or too long text suggests nothing.
    """
    text = text.strip()
    if not text or len(text) > _PUBLISHER_MAX_LENGTH:
        return []
    return [
        publisher
        for publisher in session.scalars(
            select(BookRow.publisher)
            .distinct()
            .where(_starts_with(BookRow.publisher, text, _PUBLISHER_MAX_LENGTH))
            .order_by(BookRow.publisher)
            .limit(SUGGESTIONS_LIMIT)
        )
        if publisher is not None
    ]


def suggest_categories(session: Session, text: str) -> list[str]:
    """
    Up to SUGGESTIONS_LIMIT categories used in the catalogue that start with
    `text`, ignoring case; spellings differing only in case are one
    category. Blank or too long text suggests nothing.
    """
    text = text.strip()
    if not text or len(text) > CATEGORY_MAX_LENGTH:
        return []
    return list(
        session.scalars(
            select(BookCategoryRow.category)
            .distinct()
            .where(_starts_with(BookCategoryRow.category, text, CATEGORY_MAX_LENGTH))
            .order_by(BookCategoryRow.category)
            .limit(SUGGESTIONS_LIMIT)
        )
    )


# ID, TITLE, ISBN, PUBLICATION_YEAR, then one author's NAME and SURNAME.
type _SuggestionRow = tuple[UUID, str, str | None, int | None, str | None, str | None]


def _suggestions(rows: Iterable[_SuggestionRow]) -> list[BookSuggestion]:
    """Group the per-author rows into books, keeping their order."""
    books: dict[UUID, BookSuggestion] = {}
    for book_id, title, isbn, year, name, surname in rows:
        book = books.get(book_id) or BookSuggestion(book_id, title, isbn, year)
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
    session: Session, text: str, field: BookField
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
            condition = _starts_with(BookRow.title, text, _TITLE_MAX_LENGTH)
            order = ["title", "id"]
        case BookField.ISBN:
            isbns = _isbn_suggestion_texts(text)
            if isbns is None:
                return []
            condition = or_(
                *(
                    _starts_with(BookRow.isbn, isbn, _ISBN_TEXT_MAX_LENGTH)
                    for isbn in isbns
                )
            )
            order = ["isbn"]
    # Up to SUGGESTIONS_LIMIT books, then a row per author as in the lists
    # (one with None for a book without authors).
    books = select(BookRow.id, BookRow.title, BookRow.isbn, BookRow.publication_year)
    found = (
        books.where(condition)
        .order_by(*(books.selected_columns[name] for name in order))
        .limit(SUGGESTIONS_LIMIT)
        .subquery("s")
    )
    rows = session.execute(
        select(
            found.c.id,
            found.c.title,
            found.c.isbn,
            found.c.publication_year,
            AuthorRow.name,
            AuthorRow.surname,
        )
        .select_from(found)
        .outerjoin(BookAuthorRow, BookAuthorRow.book_id == found.c.id)
        .outerjoin(AuthorRow, AuthorRow.id == BookAuthorRow.author_id)
        .order_by(*(found.c[name] for name in order), BookAuthorRow.sort_order)
    )
    return _suggestions(rows)
