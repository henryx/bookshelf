"""Finding one catalogue book: by ID or ISBN (as in the lists), or with every field."""

from collections.abc import Iterable
from dataclasses import replace
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session, selectinload

from bookshelf.db.orm import AuthorRow, BookAuthorRow, BookRow, CollectionBookRow
from bookshelf.models.books.model import Author, Book, BookDetails

__all__ = [
    "BookListRow",
    "books_from_rows",
    "find_book",
    "find_book_by_isbn",
    "find_book_details",
]

# A book list's row: ID, TITLE, PUBLICATION_YEAR, the user's COPIES and
# IS_READ, then one author's NAME, SURNAME, ID (all None for a book without
# authors).
type BookListRow = tuple[
    UUID, str, int | None, int | None, bool | None, str | None, str | None, UUID | None
]


def books_from_rows(rows: Iterable[BookListRow]) -> list[Book]:
    """Group the per-author rows into books, keeping their order."""
    books: dict[UUID, Book] = {}
    for book_id, title, year, copies, read, name, surname, author_id in rows:
        book = books.get(book_id) or Book(
            book_id, title, (), year, copies, read=bool(read)
        )
        if surname is not None and author_id is not None:
            author = Author(name or "", surname, id=author_id)
            book = replace(book, authors=(*book.authors, author))
        books[book_id] = book
    return list(books.values())


def _one_book(
    session: Session, user_id: UUID, condition: ColumnElement[bool]
) -> Book | None:
    """The book matching `condition`, a row per author as in the lists."""
    rows = session.execute(
        select(
            BookRow.id,
            BookRow.title,
            BookRow.publication_year,
            CollectionBookRow.copies,
            CollectionBookRow.is_read,
            AuthorRow.name,
            AuthorRow.surname,
            AuthorRow.id,
        )
        .outerjoin(
            CollectionBookRow,
            and_(
                CollectionBookRow.book_id == BookRow.id,
                CollectionBookRow.user_id == user_id,
            ),
        )
        .outerjoin(BookAuthorRow, BookAuthorRow.book_id == BookRow.id)
        .outerjoin(AuthorRow, AuthorRow.id == BookAuthorRow.author_id)
        .where(condition)
        .order_by(BookAuthorRow.sort_order)
    )
    books = books_from_rows(rows)
    return books[0] if books else None


def find_book(session: Session, user_id: UUID, book_id: UUID) -> Book | None:
    """The catalogue book `book_id`, with the copies `user_id` owns; None if unknown."""
    return _one_book(session, user_id, BookRow.id == book_id)


def find_book_details(session: Session, book_id: UUID) -> BookDetails | None:
    """Every field of the catalogue book `book_id`; None if unknown."""
    book = session.scalars(
        select(BookRow)
        .where(BookRow.id == book_id)
        .options(selectinload(BookRow.authors), selectinload(BookRow.categories))
    ).one_or_none()
    if book is None:
        return None
    collections = session.scalar(
        select(func.count()).where(CollectionBookRow.book_id == book_id)
    )
    return BookDetails(
        id=book_id,
        title=book.title,
        authors=tuple(Author(ba.author.name, ba.author.surname) for ba in book.authors),
        isbn=book.isbn,
        publisher=book.publisher,
        publication_year=book.publication_year,
        series=book.series,
        volume=book.volume,
        plot=book.plot,
        categories=tuple(category.category for category in book.categories),
        collections=collections or 0,
    )


def find_book_by_isbn(session: Session, user_id: UUID, isbn: str) -> Book | None:
    """The catalogue book with this ISBN-13, as find_book; None if there is none."""
    return _one_book(session, user_id, BookRow.isbn == isbn)
