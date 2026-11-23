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
    MAX_COPIES,
    UNIQUE_KEY_VIOLATION,
    CopiedCollection,
    CopyMode,
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


def copy_collection(
    conn: Connection, source_id: UUID, target_id: UUID, mode: CopyMode
) -> CopiedCollection:
    """
    Copy the books of one user's collection, with their copies, into
    another's; `mode` says what happens to the books the target already has.
    Copied books start unread: what someone has read is their own. The
    source's collection is unchanged. ValueError when both are the same user.

    One read committed transaction of its own, as in _change_entry: all of it
    or nothing, and run again when a target entry it adds copies to is being
    removed meanwhile.
    """
    if source_id == target_id:
        raise ValueError("A collection cannot be copied to its own user.")
    source, target = source_id.bytes, target_id.bytes

    def copy() -> CopiedCollection:
        removed = updated = 0
        with (
            conn.transaction_manager(
                tpb(Isolation.READ_COMMITTED_RECORD_VERSION),
                default_action=DefaultAction.ROLLBACK,
            ) as transaction,
            transaction.cursor() as cur,
        ):
            match mode:
                case CopyMode.REPLACE:
                    cur.execute(
                        "DELETE FROM COLLECTION_BOOKS WHERE USER_ID = ?", (target,)
                    )
                    removed = cur.affected_rows
                case CopyMode.ADD:
                    cur.execute(
                        "UPDATE COLLECTION_BOOKS t SET COPIES = MINVALUE("
                        "t.COPIES + (SELECT s.COPIES FROM COLLECTION_BOOKS s "
                        "WHERE s.USER_ID = ? AND s.BOOK_ID = t.BOOK_ID), "
                        "CAST(? AS SMALLINT)) "
                        "WHERE t.USER_ID = ? AND EXISTS (SELECT 1 FROM COLLECTION_BOOKS s "
                        "WHERE s.USER_ID = ? AND s.BOOK_ID = t.BOOK_ID)",
                        (source, MAX_COPIES, target, source),
                    )
                    updated = cur.affected_rows
                case CopyMode.KEEP:
                    pass
            cur.execute(
                "INSERT INTO COLLECTION_BOOKS (USER_ID, BOOK_ID, COPIES, IS_READ) "
                "SELECT CAST(? AS UUID), s.BOOK_ID, s.COPIES, FALSE "
                "FROM COLLECTION_BOOKS s WHERE s.USER_ID = ? AND NOT EXISTS ("
                "SELECT 1 FROM COLLECTION_BOOKS t "
                "WHERE t.USER_ID = ? AND t.BOOK_ID = s.BOOK_ID)",
                (target, source, target),
            )
            added = cur.affected_rows
            transaction.commit()
        return CopiedCollection(added=added, updated=updated, removed=removed)

    copied = retry_after_concurrent_delete(copy)
    if conn.is_active():
        conn.commit()
    return copied


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
