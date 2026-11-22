"""Adding, editing and deleting catalogue books, with their authors and categories."""

import logging
from uuid import UUID, uuid4

from firebird.driver import (
    Connection,
    Cursor,
    DatabaseError,
    DefaultAction,
    Isolation,
    tpb,
)

from bookshelf.db import retry_after_concurrent_delete
from bookshelf.models.books.isbn import normalise_isbn
from bookshelf.models.books.model import (
    FOREIGN_KEY_VIOLATION,
    UNIQUE_KEY_VIOLATION,
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
        if UNIQUE_KEY_VIOLATION not in e.gds_codes or existing is None:
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
            if FOREIGN_KEY_VIOLATION not in e.gds_codes:
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
        if isbn is not None and UNIQUE_KEY_VIOLATION in e.gds_codes:
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
        if UNIQUE_KEY_VIOLATION in e.gds_codes:
            raise BookExistsError(isbn) from e
        raise
    if cur.affected_rows == 0:
        raise IsbnAlreadySetError(book_id)


def _replace_isbn(cur: Cursor, book_id: UUID, isbn: str | None) -> None:
    """Set a book's ISBN, whatever it was, or remove it (None)."""
    try:
        cur.execute("UPDATE BOOKS SET ISBN = ? WHERE ID = ?", (isbn, book_id.bytes))
    except DatabaseError as e:
        if isbn is not None and UNIQUE_KEY_VIOLATION in e.gds_codes:
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
    Raises UnknownBookError for an unknown book (even one deleted at this
    moment) and BookInCollectionError if it is in any user's collection (even
    one added at this moment); nothing changes then.

    The transaction is its own and read committed, as in update_book.
    """
    return retry_after_concurrent_delete(lambda: _delete_book(conn, book_id))


def _delete_book(conn: Connection, book_id: UUID) -> DeletedBook:
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
            if FOREIGN_KEY_VIOLATION in e.gds_codes:
                raise BookInCollectionError(title) from e
            raise
        if cur.affected_rows == 0:
            raise UnknownBookError(book_id)
        deleted = _delete_unused_authors(cur, authors)
        transaction.commit()
    return DeletedBook(title, tuple(deleted))
