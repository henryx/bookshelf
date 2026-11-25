"""The authors page's list, and finding one author."""

from uuid import UUID

from sqlalchemy import ColumnElement, Row, String, func, literal, select
from sqlalchemy.orm import Session

from bookshelf.db.orm import AuthorRow, BookAuthorRow
from bookshelf.db.sql import matches_search
from bookshelf.models.authors.model import AuthorEntry, AuthorPage
from bookshelf.models.books import PAGE_SIZE, SEARCH_MAX_LENGTH, Author

__all__ = ["find_author", "list_authors"]


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
