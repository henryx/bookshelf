"""A user's collection: adding a catalogue book, copies, read, removing."""

from uuid import UUID

from firebird.driver import Connection, DatabaseError

from bookshelf.models.books.model import (
    FOREIGN_KEY_VIOLATION,
    UNIQUE_KEY_VIOLATION,
    UnknownBookError,
)


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
        if FOREIGN_KEY_VIOLATION in e.gds_codes:
            raise UnknownBookError(book_id) from e
        if UNIQUE_KEY_VIOLATION not in e.gds_codes:
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
