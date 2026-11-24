"""A user's collection: adding a catalogue book, copies, read, removing."""

from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Executable,
    SmallInteger,
    delete,
    exists,
    false,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, aliased

from bookshelf.db import (
    execute_change,
    is_foreign_key_violation,
    is_unique_violation,
    read_committed,
    retry_after_concurrent_delete,
    try_change,
)
from bookshelf.db.orm import CollectionBookRow, UUIDType
from bookshelf.db.sql import least
from bookshelf.models.books.model import (
    MAX_COPIES,
    CopiedCollection,
    CopyMode,
    UnknownBookError,
)


def add_to_collection(
    session: Session,
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
        try_change(
            session,
            insert(CollectionBookRow).values(
                user_id=user_id, book_id=book_id, copies=copies, is_read=read
            ),
        )
    except DBAPIError as e:
        if is_foreign_key_violation(e):
            raise UnknownBookError(book_id) from e
        if not is_unique_violation(e):
            raise
        return False
    session.commit()
    return True


def set_copies(session: Session, user_id: UUID, book_id: UUID, copies: int) -> bool:
    """Set how many copies of a book the user owns; False if it is not in their collection."""
    return _change_entry(
        session,
        update(CollectionBookRow).where(_entry(user_id, book_id)).values(copies=copies),
    )


def set_read(session: Session, user_id: UUID, book_id: UUID, *, read: bool) -> bool:
    """Mark a book in the user's collection as read or not; False if it is not there."""
    return _change_entry(
        session,
        update(CollectionBookRow).where(_entry(user_id, book_id)).values(is_read=read),
    )


def remove_from_collection(session: Session, user_id: UUID, book_id: UUID) -> bool:
    """
    Remove a book from the user's collection, leaving it in the catalogue.
    False if it was not there.
    """
    return _change_entry(
        session, delete(CollectionBookRow).where(_entry(user_id, book_id))
    )


def _entry(user_id: UUID, book_id: UUID) -> ColumnElement[bool]:
    return (CollectionBookRow.user_id == user_id) & (
        CollectionBookRow.book_id == book_id
    )


def copy_collection(
    session: Session, source_id: UUID, target_id: UUID, mode: CopyMode
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
    # The target's entries are CollectionBookRow itself; the source's, `source`.
    source = aliased(CollectionBookRow)
    target = CollectionBookRow
    source_entry = (source.user_id == source_id) & (source.book_id == target.book_id)

    def copy() -> CopiedCollection:
        removed = updated = 0
        with read_committed(session):
            match mode:
                case CopyMode.REPLACE:
                    removed = execute_change(
                        session, delete(target).where(target.user_id == target_id)
                    ).rowcount
                case CopyMode.ADD:
                    source_copies = select(source.copies).where(source_entry)
                    updated = execute_change(
                        session,
                        update(target)
                        .where(
                            target.user_id == target_id, exists().where(source_entry)
                        )
                        .values(
                            copies=least(
                                target.copies + source_copies.scalar_subquery(),
                                literal(MAX_COPIES, SmallInteger),
                            )
                        ),
                    ).rowcount
                case CopyMode.KEEP:
                    pass
            # The source's books the target does not have, unread.
            owned = aliased(CollectionBookRow)
            added = execute_change(
                session,
                insert(target).from_select(
                    ["user_id", "book_id", "copies", "is_read"],
                    select(
                        literal(target_id, UUIDType),
                        source.book_id,
                        source.copies,
                        false(),
                    ).where(
                        source.user_id == source_id,
                        ~exists().where(
                            owned.user_id == target_id,
                            owned.book_id == source.book_id,
                        ),
                    ),
                ),
            ).rowcount
            session.commit()
        return CopiedCollection(added=added, updated=updated, removed=removed)

    return retry_after_concurrent_delete(copy)


def _change_entry(session: Session, statement: Executable) -> bool:
    """
    Run `statement`, changing one user's collection entry; False if there is
    none, even when it is being removed at this moment.

    The transaction is its own and read committed, as in update_book, so it
    sees an entry removed since the request's transaction began; the request
    then reads the change.
    """

    def change() -> bool:
        with read_committed(session):
            changed = execute_change(session, statement).rowcount > 0
            session.commit()
        return changed

    return retry_after_concurrent_delete(change)
