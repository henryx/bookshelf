import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, Row, String, delete, func, literal, select, update
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
from bookshelf.db.orm import AuthorRow, BookAuthorRow
from bookshelf.db.sql import matches_search
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


def _matches(search: str) -> ColumnElement[bool]:
    """The author's "name surname" matches the search (see matches_search)."""
    full_name = AuthorRow.name + literal(" ", String(1)) + AuthorRow.surname
    return matches_search(full_name, literal(search, String(SEARCH_MAX_LENGTH)))


# An author's ID, NAME, SURNAME and the number of their books.
_ENTRY_COLUMNS = (
    AuthorRow.id,
    AuthorRow.name,
    AuthorRow.surname,
    select(func.count())
    .where(BookAuthorRow.author_id == AuthorRow.id)
    .scalar_subquery(),
)


def _entry(row: Row[UUID, str, str, int]) -> AuthorEntry:
    author_id, name, surname, books = row
    return AuthorEntry(author_id, Author(name, surname), books)


def list_authors(
    session: Session, search: str = "", page: int = 1, *, page_size: int = PAGE_SIZE
) -> AuthorPage:
    """
    One page of the authors, by surname then name, filtered by `search`
    (in "name surname", ignoring case). A page out of range gives the
    nearest existing page.
    """
    search = search.strip()
    if len(search) > SEARCH_MAX_LENGTH:
        return AuthorPage([], 0, 1, page_size)
    total = session.scalar(select(func.count()).where(_matches(search))) or 0
    page = min(max(page, 1), AuthorPage([], total, 1, page_size).pages)
    rows = session.execute(
        select(*_ENTRY_COLUMNS)
        .where(_matches(search))
        .order_by(AuthorRow.surname, AuthorRow.name, AuthorRow.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return AuthorPage([_entry(row) for row in rows], total, page, page_size)


def find_author(session: Session, author_id: UUID) -> AuthorEntry | None:
    row = session.execute(
        select(*_ENTRY_COLUMNS).where(AuthorRow.id == author_id)
    ).one_or_none()
    return _entry(row) if row else None


def _namesake(
    session: Session, author_id: UUID, author: Author
) -> tuple[UUID, Author] | None:
    """Another author with this name and surname (ignoring case), as stored."""
    row = session.execute(
        select(AuthorRow.id, AuthorRow.name, AuthorRow.surname).where(
            AuthorRow.surname == author.surname,
            AuthorRow.name == author.name,
            AuthorRow.id != author_id,
        )
    ).first()
    return (row.id, Author(row.name, row.surname)) if row else None


def _rename(session: Session, author_id: UUID, author: Author) -> bool:
    """Give the author this name; False if another author took it meanwhile."""
    try:
        result = try_change(
            session,
            update(AuthorRow)
            .where(AuthorRow.id == author_id)
            .values(name=author.name, surname=author.surname),
        )
    except DBAPIError as e:
        if not is_unique_violation(e):
            raise
        logger.info(
            "Author %s: %r was created meanwhile, merging into it",
            author_id,
            str(author),
        )
        return False
    if result.rowcount == 0:
        raise UnknownAuthorError(author_id)
    return True


def _merge(session: Session, author_id: UUID, into: UUID) -> None:
    """
    Give the author's books to the author `into` and delete the author. A
    book that has both keeps `into`, in its own place.
    """
    books_of_into = select(BookAuthorRow.book_id).where(BookAuthorRow.author_id == into)
    execute_change(
        session,
        delete(BookAuthorRow).where(
            BookAuthorRow.author_id == author_id,
            BookAuthorRow.book_id.in_(books_of_into),
        ),
    )
    execute_change(
        session,
        update(BookAuthorRow)
        .where(BookAuthorRow.author_id == author_id)
        .values(author_id=into),
    )
    try:
        result = try_change(session, delete(AuthorRow).where(AuthorRow.id == author_id))
    except DBAPIError as e:
        # A book being added at this moment uses it: keep it, with that book.
        if not is_foreign_key_violation(e):
            raise
        logger.info(
            "Author %s kept after merging: a book being saved at this moment uses it",
            author_id,
        )
        return
    if result.rowcount == 0:
        raise UnknownAuthorError(author_id)


def rename_author(session: Session, author_id: UUID, author: Author) -> Renamed:
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
        lambda: _rename_author(session, author_id, author)
    )


def _rename_author(session: Session, author_id: UUID, author: Author) -> Renamed:
    with read_committed(session):
        for _ in range(_RENAME_TRIES):
            namesake = _namesake(session, author_id, author)
            if namesake is not None:
                into, existing = namesake
                _merge(session, author_id, into)
                session.commit()
                return Renamed(existing, merged=True)
            if _rename(session, author_id, author):
                session.commit()
                return Renamed(author, merged=False)
            # Another author took the name meanwhile: merge into them, or try
            # the rename again if they have already given it up.
        raise RuntimeError(
            f"Author {author_id}: the name {str(author)!r} kept being taken "
            "and given up by another author"
        )
