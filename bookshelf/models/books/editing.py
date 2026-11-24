"""Adding, editing and deleting catalogue books, with their authors and categories."""

import logging
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.db import (
    FOREIGN_KEY_VIOLATION,
    UNIQUE_KEY_VIOLATION,
    execute_change,
    has_gds_code,
    read_committed,
    retry_after_concurrent_delete,
)
from bookshelf.db.orm import (
    AuthorRow,
    BookAuthorRow,
    BookCategoryRow,
    BookRow,
    CollectionBookRow,
)
from bookshelf.models.books.isbn import normalise_isbn
from bookshelf.models.books.model import (
    Author,
    BookExistsError,
    BookInCollectionError,
    DeletedBook,
    IsbnAlreadySetError,
    NewBook,
    UnknownBookError,
)

logger = logging.getLogger(__name__)


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


def _find_author(session: Session, author: Author) -> UUID | None:
    # Both ignore case (UNICODE_CI), so "TERRY PRATCHETT" finds "Terry Pratchett".
    return session.scalar(
        select(AuthorRow.id).where(
            AuthorRow.surname == author.surname, AuthorRow.name == author.name
        )
    )


def _author_id(session: Session, author: Author) -> UUID:
    """The existing author with this name and surname (ignoring case), or a new one."""
    author_id = _find_author(session, author)
    if author_id is not None:
        return author_id
    author_id = uuid4()
    try:
        execute_change(
            session,
            insert(AuthorRow).values(
                id=author_id, name=author.name, surname=author.surname
            ),
        )
    except DBAPIError as e:
        # Committed by someone else since the lookup: use theirs. The
        # transaction is read committed (see add_book), so it sees that row.
        existing = _find_author(session, author)
        if not has_gds_code(e, UNIQUE_KEY_VIOLATION) or existing is None:
            raise
        return existing
    return author_id


def _insert_authors(
    session: Session, book_id: UUID, authors: tuple[Author, ...]
) -> None:
    """Link a book (with no authors yet) to these authors, in order: see unique_authors."""
    for order, author in enumerate(unique_authors(authors), start=1):
        execute_change(
            session,
            insert(BookAuthorRow).values(
                book_id=book_id,
                author_id=_author_id(session, author),
                sort_order=order,
            ),
        )


def _replace_authors(
    session: Session, book_id: UUID, authors: tuple[Author, ...]
) -> None:
    """
    Make these the book's authors, in order. A former author that no book
    uses any more is deleted, so a misspelled name stops being suggested.
    """
    former = _author_ids(session, book_id)
    # All at once: the rows are checked one by one, so they cannot be reordered in place.
    execute_change(
        session, delete(BookAuthorRow).where(BookAuthorRow.book_id == book_id)
    )
    _insert_authors(session, book_id, authors)
    _delete_unused_authors(session, former)


def _author_ids(session: Session, book_id: UUID) -> list[UUID]:
    return list(
        session.scalars(
            select(BookAuthorRow.author_id).where(BookAuthorRow.book_id == book_id)
        )
    )


def _delete_unused_authors(session: Session, author_ids: list[UUID]) -> list[UUID]:
    """Delete each of these authors that no book uses any more; the ones deleted."""
    deleted = []
    for author_id in author_ids:
        try:
            result = execute_change(
                session,
                delete(AuthorRow).where(
                    AuthorRow.id == author_id,
                    ~select(BookAuthorRow.author_id)
                    .where(BookAuthorRow.author_id == AuthorRow.id)
                    .exists(),
                ),
            )
        except DBAPIError as e:
            # A book being added at this moment uses it: keep it.
            if not has_gds_code(e, FOREIGN_KEY_VIOLATION):
                raise
            logger.info(
                "Author %s kept: a book being saved at this moment uses it", author_id
            )
            continue
        if result.rowcount > 0:
            deleted.append(author_id)
    return deleted


def _insert_categories(
    session: Session, book_id: UUID, categories: tuple[str, ...]
) -> None:
    for category in unique_categories(categories):
        execute_change(
            session,
            insert(BookCategoryRow).values(book_id=book_id, category=category),
        )


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
        execute_change(
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
        if isbn is not None and has_gds_code(e, UNIQUE_KEY_VIOLATION):
            raise BookExistsError(isbn) from e
        raise
    _insert_authors(session, book_id, book.authors)
    _insert_categories(session, book_id, book.categories)
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
        result = execute_change(
            session,
            update(BookRow)
            .where(BookRow.id == book_id, BookRow.isbn.is_(None))
            .values(isbn=isbn),
        )
    except DBAPIError as e:
        if has_gds_code(e, UNIQUE_KEY_VIOLATION):
            raise BookExistsError(isbn) from e
        raise
    if result.rowcount == 0:
        raise IsbnAlreadySetError(book_id)


def _replace_isbn(session: Session, book_id: UUID, isbn: str | None) -> None:
    """Set a book's ISBN, whatever it was, or remove it (None)."""
    try:
        execute_change(
            session, update(BookRow).where(BookRow.id == book_id).values(isbn=isbn)
        )
    except DBAPIError as e:
        if isbn is not None and has_gds_code(e, UNIQUE_KEY_VIOLATION):
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
        _replace_authors(session, book_id, authors)
        execute_change(
            session, delete(BookCategoryRow).where(BookCategoryRow.book_id == book_id)
        )
        _insert_categories(session, book_id, categories)
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
        authors = _author_ids(session, book_id)
        try:
            # Its BOOK_AUTHORS and BOOK_CATEGORIES rows go with it (ON DELETE CASCADE).
            result = execute_change(
                session, delete(BookRow).where(BookRow.id == book_id)
            )
        except DBAPIError as e:
            # FK_COLLECTION_BOOKS_BOOK: someone owns it.
            if has_gds_code(e, FOREIGN_KEY_VIOLATION):
                raise BookInCollectionError(title) from e
            raise
        if result.rowcount == 0:
            raise UnknownBookError(book_id)
        deleted = _delete_unused_authors(session, authors)
        session.commit()
    return DeletedBook(title, tuple(deleted))
