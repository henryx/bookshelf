"""A user's collection: adding a catalogue book, copies, read, removing."""

from uuid import UUID

from firebird.driver import (
    Connection,
    DatabaseError,
    DefaultAction,
    Isolation,
    tpb,
)

from bookshelf.db import retry_after_concurrent_delete
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
    return _change_entry(
        conn,
        "UPDATE COLLECTION_BOOKS SET COPIES = ? WHERE USER_ID = ? AND BOOK_ID = ?",
        (copies, user_id.bytes, book_id.bytes),
    )


def set_read(conn: Connection, user_id: UUID, book_id: UUID, *, read: bool) -> bool:
    """Mark a book in the user's collection as read or not; False if it is not there."""
    return _change_entry(
        conn,
        "UPDATE COLLECTION_BOOKS SET IS_READ = ? WHERE USER_ID = ? AND BOOK_ID = ?",
        (read, user_id.bytes, book_id.bytes),
    )


def remove_from_collection(conn: Connection, user_id: UUID, book_id: UUID) -> bool:
    """
    Remove a book from the user's collection, leaving it in the catalogue.
    False if it was not there.
    """
    return _change_entry(
        conn,
        "DELETE FROM COLLECTION_BOOKS WHERE USER_ID = ? AND BOOK_ID = ?",
        (user_id.bytes, book_id.bytes),
    )


def _change_entry(conn: Connection, sql: str, params: tuple[object, ...]) -> bool:
    """
    Run `sql`, changing one user's collection entry; False if there is none,
    even when it is being removed at this moment.

    The transaction is its own and read committed, as in update_book, so it
    sees an entry removed since the request's own transaction began. That one
    ends too, so the request then reads the change.
    """

    def change() -> bool:
        with (
            conn.transaction_manager(
                tpb(Isolation.READ_COMMITTED_RECORD_VERSION),
                default_action=DefaultAction.ROLLBACK,
            ) as transaction,
            transaction.cursor() as cur,
        ):
            cur.execute(sql, params)
            changed = cur.affected_rows > 0
            transaction.commit()
        return changed

    changed = retry_after_concurrent_delete(change)
    if conn.is_active():
        conn.commit()
    return changed
