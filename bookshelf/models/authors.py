import logging
from dataclasses import dataclass
from uuid import UUID

from firebird.driver import (
    Connection,
    Cursor,
    DatabaseError,
    DefaultAction,
    Isolation,
    tpb,
)

from bookshelf.db import retry_after_concurrent_delete
from bookshelf.models.books import PAGE_SIZE, SEARCH_MAX_LENGTH, Author

__all__ = [
    "AuthorEntry",
    "AuthorPage",
    "Renamed",
    "UnknownAuthorError",
    "find_author",
    "list_authors",
    "rename_author",
]

# Firebird GDS codes for "violation of PRIMARY or UNIQUE KEY constraint" and
# "violation of FOREIGN KEY constraint".
_UNIQUE_KEY_VIOLATION = 335544665
_FOREIGN_KEY_VIOLATION = 335544466
# Renames tried when another author takes the name and gives it up again.
_RENAME_TRIES = 10

logger = logging.getLogger(__name__)


class UnknownAuthorError(Exception):
    """No author has this ID (it may have just been merged into another)."""

    def __init__(self, author_id: UUID) -> None:
        super().__init__(f"No author {author_id}.")
        self.author_id = author_id


@dataclass(frozen=True)
class AuthorEntry:
    """An author as the authors page lists it: with how many books are theirs."""

    id: UUID
    author: Author
    books: int


@dataclass(frozen=True)
class AuthorPage:
    authors: list[AuthorEntry]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        """Number of pages; at least 1, so an empty list still has a page."""
        return max(1, -(-self.total // self.page_size))


@dataclass(frozen=True)
class Renamed:
    """
    What rename_author did: the author's name now, as stored, and whether it
    was merged into another author who already had that name.
    """

    author: Author
    merged: bool


# Search: the author's "name surname" contains the text, ignoring case.
_COUNT_QUERY = """
    SELECT COUNT(*) FROM AUTHORS a
    WHERE (a.NAME || ' ' || a.SURNAME) CONTAINING CAST(? AS VARCHAR(255))
"""

_LIST_QUERY = """
    SELECT a.ID, a.NAME, a.SURNAME,
           (SELECT COUNT(*) FROM BOOK_AUTHORS ba WHERE ba.AUTHOR_ID = a.ID)
    FROM AUTHORS a
    WHERE (a.NAME || ' ' || a.SURNAME) CONTAINING CAST(? AS VARCHAR(255))
    ORDER BY a.SURNAME, a.NAME, a.ID
    OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
"""

type _AuthorRow = tuple[bytes, str, str, int]


def _entry(row: _AuthorRow) -> AuthorEntry:
    author_id, name, surname, books = row
    return AuthorEntry(UUID(bytes=author_id), Author(name, surname), books)


def list_authors(
    conn: Connection, search: str = "", page: int = 1, *, page_size: int = PAGE_SIZE
) -> AuthorPage:
    """
    One page of the authors, by surname then name, filtered by `search`
    (in "name surname", ignoring case). A page out of range gives the
    nearest existing page.
    """
    search = search.strip()
    if len(search) > SEARCH_MAX_LENGTH:
        return AuthorPage([], 0, 1, page_size)
    with conn.cursor() as cur:
        cur.execute(_COUNT_QUERY, (search,))
        (total,) = cur.fetchone()
        page = min(max(page, 1), AuthorPage([], total, 1, page_size).pages)
        cur.execute(_LIST_QUERY, (search, (page - 1) * page_size, page_size))
        return AuthorPage(
            [_entry(row) for row in cur.fetchall()], total, page, page_size
        )


def find_author(conn: Connection, author_id: UUID) -> AuthorEntry | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.ID, a.NAME, a.SURNAME, "
            "(SELECT COUNT(*) FROM BOOK_AUTHORS ba WHERE ba.AUTHOR_ID = a.ID) "
            "FROM AUTHORS a WHERE a.ID = ?",
            (author_id.bytes,),
        )
        row = cur.fetchone()
    return _entry(row) if row else None


def _namesake(
    cur: Cursor, author_id: UUID, author: Author
) -> tuple[bytes, Author] | None:
    """Another author with this name and surname (ignoring case), as stored."""
    cur.execute(
        "SELECT ID, NAME, SURNAME FROM AUTHORS WHERE SURNAME = ? AND NAME = ? AND ID <> ?",
        (author.surname, author.name, author_id.bytes),
    )
    row = cur.fetchone()
    return (row[0], Author(row[1], row[2])) if row else None


def _rename(cur: Cursor, author_id: UUID, author: Author) -> bool:
    """Give the author this name; False if another author took it meanwhile."""
    try:
        cur.execute(
            "UPDATE AUTHORS SET NAME = ?, SURNAME = ? WHERE ID = ?",
            (author.name, author.surname, author_id.bytes),
        )
    except DatabaseError as e:
        if _UNIQUE_KEY_VIOLATION not in e.gds_codes:
            raise
        logger.info(
            "Author %s: %r was created meanwhile, merging into it",
            author_id,
            str(author),
        )
        return False
    if cur.affected_rows == 0:
        raise UnknownAuthorError(author_id)
    return True


def _merge(cur: Cursor, author_id: UUID, into: bytes) -> None:
    """
    Give the author's books to the author `into` and delete the author. A
    book that has both keeps `into`, in its own place.
    """
    cur.execute(
        "DELETE FROM BOOK_AUTHORS WHERE AUTHOR_ID = ? AND BOOK_ID IN ("
        "SELECT BOOK_ID FROM BOOK_AUTHORS WHERE AUTHOR_ID = ?)",
        (author_id.bytes, into),
    )
    cur.execute(
        "UPDATE BOOK_AUTHORS SET AUTHOR_ID = ? WHERE AUTHOR_ID = ?",
        (into, author_id.bytes),
    )
    try:
        cur.execute("DELETE FROM AUTHORS WHERE ID = ?", (author_id.bytes,))
    except DatabaseError as e:
        # A book being added at this moment uses it: keep it, with that book.
        if _FOREIGN_KEY_VIOLATION not in e.gds_codes:
            raise
        logger.info(
            "Author %s kept after merging: a book being saved at this moment uses it",
            author_id,
        )
        return
    if cur.affected_rows == 0:
        raise UnknownAuthorError(author_id)


def rename_author(conn: Connection, author_id: UUID, author: Author) -> Renamed:
    """
    Rename an author for every book, name and surname trimmed. When another
    author already has that name (ignoring case) the two are merged: their
    books all get that author, as spelled, and this one is deleted. Raises
    UnknownAuthorError for an unknown author (even one deleted at this
    moment); nothing changes then. When another author takes the name and
    gives it up again before it can be merged into, the rename is tried
    again, up to 10 times in all.

    The transaction is its own and read committed, as in add_book, so it
    sees an author with that name committed by someone else meanwhile.
    """
    author = Author(author.name.strip(), author.surname.strip())
    return retry_after_concurrent_delete(
        lambda: _rename_author(conn, author_id, author)
    )


def _rename_author(conn: Connection, author_id: UUID, author: Author) -> Renamed:
    with (
        conn.transaction_manager(
            tpb(Isolation.READ_COMMITTED_RECORD_VERSION),
            default_action=DefaultAction.ROLLBACK,
        ) as transaction,
        transaction.cursor() as cur,
    ):
        for _ in range(_RENAME_TRIES):
            namesake = _namesake(cur, author_id, author)
            if namesake is not None:
                into, existing = namesake
                _merge(cur, author_id, into)
                transaction.commit()
                return Renamed(existing, merged=True)
            if _rename(cur, author_id, author):
                transaction.commit()
                return Renamed(author, merged=False)
            # Another author took the name meanwhile: merge into them, or try
            # the rename again if they have already given it up.
        raise RuntimeError(
            f"Author {author_id}: the name {str(author)!r} kept being taken "
            "and given up by another author"
        )
