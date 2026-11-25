"""Adding, editing and deleting catalogue books (their authors and categories: links.py)."""

from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.db import (
    execute_change,
    is_foreign_key_violation,
    is_unique_violation,
    read_committed,
    retry_after_concurrent_delete,
    try_change,
)
from bookshelf.db.orm import BookCategoryRow, BookRow, CollectionBookRow
from bookshelf.models.books.isbn import normalise_isbn
from bookshelf.models.books.links import (
    author_ids,
    delete_unused_authors,
    insert_authors,
    insert_categories,
    replace_authors,
)
from bookshelf.models.books.model import (
    Author,
    BookExistsError,
    BookInCollectionError,
    DeletedBook,
    IsbnAlreadySetError,
    NewBook,
    UnknownBookError,
)

__all__ = ["add_book", "delete_book", "update_book"]


def _insert_book(
    session: Session,
    user_id: UUID,
    book_id: UUID,
    book: NewBook,
    isbn: str | None,
    copies: int,
    read: bool,
) -> None:
    try:
        try_change(
            session,
            insert(BookRow).values(
                id=book_id,
                title=book.title.strip(),
                isbn=isbn,
                publisher=book.publisher,
                publication_year=book.publication_year,
                series=book.series,
                volume=book.volume,
                plot=book.plot,
            ),
        )
    except DBAPIError as e:
        # The ID is new, so the only key that can clash is the ISBN.
        if isbn is not None and is_unique_violation(e):
            raise BookExistsError(isbn) from e
        raise
    insert_authors(session, book_id, book.authors)
    insert_categories(session, book_id, book.categories)
    execute_change(
        session,
        insert(CollectionBookRow).values(
            user_id=user_id, book_id=book_id, copies=copies, is_read=read
        ),
    )


def add_book(
    session: Session,
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

    The transaction is its own and read committed (see read_committed), so
    it can use an author committed by someone else after the request began.
    """
    isbn = normalise_isbn(book.isbn) if book.isbn else None
    book_id = uuid4()
    with read_committed(session):
        _insert_book(session, user_id, book_id, book, isbn, copies, read)
        session.commit()
    return book_id


def _set_isbn(session: Session, book_id: UUID, isbn: str) -> None:
    """
    Give a book without an ISBN this one. The ISBN IS NULL condition keeps
    an ISBN from changing, even when the caller's view of the book is out of
    date: only _replace_isbn (admins) changes one.
    """
    try:
        result = try_change(
            session,
            update(BookRow)
            .where(BookRow.id == book_id, BookRow.isbn.is_(None))
            .values(isbn=isbn),
        )
    except DBAPIError as e:
        if is_unique_violation(e):
            raise BookExistsError(isbn) from e
        raise
    if result.rowcount == 0:
        raise IsbnAlreadySetError(book_id)


def _replace_isbn(session: Session, book_id: UUID, isbn: str | None) -> None:
    """Set a book's ISBN, whatever it was, or remove it (None)."""
    try:
        try_change(
            session, update(BookRow).where(BookRow.id == book_id).values(isbn=isbn)
        )
    except DBAPIError as e:
        if isbn is not None and is_unique_violation(e):
            raise BookExistsError(isbn) from e
        raise


def update_book(
    session: Session,
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
    with read_committed(session):
        result = execute_change(
            session,
            update(BookRow)
            .where(BookRow.id == book_id)
            .values(
                publisher=publisher,
                publication_year=publication_year,
                series=series,
                volume=volume,
                plot=plot,
            ),
        )
        if result.rowcount == 0:
            raise UnknownBookError(book_id)
        replace_authors(session, book_id, authors)
        execute_change(
            session, delete(BookCategoryRow).where(BookCategoryRow.book_id == book_id)
        )
        insert_categories(session, book_id, categories)
        if replace_isbn:
            _replace_isbn(session, book_id, new_isbn)
        elif new_isbn is not None:
            _set_isbn(session, book_id, new_isbn)
        session.commit()


def delete_book(session: Session, book_id: UUID) -> DeletedBook:
    """
    Delete a book for good, with its author links and categories. An author it
    leaves without books is deleted too, as when editing.
    Raises UnknownBookError for an unknown book (even one deleted at this
    moment) and BookInCollectionError if it is in any user's collection (even
    one added at this moment); nothing changes then.

    The transaction is its own and read committed, as in update_book.
    """
    return retry_after_concurrent_delete(lambda: _delete_book(session, book_id))


def _delete_book(session: Session, book_id: UUID) -> DeletedBook:
    with read_committed(session):
        title = session.scalar(select(BookRow.title).where(BookRow.id == book_id))
        if title is None:
            raise UnknownBookError(book_id)
        authors = author_ids(session, book_id)
        try:
            # Its BOOK_AUTHORS and BOOK_CATEGORIES rows go with it (ON DELETE CASCADE).
            result = try_change(session, delete(BookRow).where(BookRow.id == book_id))
        except DBAPIError as e:
            # FK_COLLECTION_BOOKS_BOOK: someone owns it.
            if is_foreign_key_violation(e):
                raise BookInCollectionError(title) from e
            raise
        if result.rowcount == 0:
            raise UnknownBookError(book_id)
        deleted = delete_unused_authors(session, authors)
        session.commit()
    return DeletedBook(title, tuple(deleted))
