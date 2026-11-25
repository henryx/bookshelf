"""
A book's authors and categories: picking or creating an author, linking a
book to its authors and categories, and deleting the authors no book uses
any more.
"""

import logging
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.db import (
    execute_change,
    is_foreign_key_violation,
    is_unique_violation,
    try_change,
)
from bookshelf.db.orm import AuthorRow, BookAuthorRow, BookCategoryRow
from bookshelf.models.books.model import Author

__all__ = [
    "author_ids",
    "delete_unused_authors",
    "insert_authors",
    "insert_categories",
    "replace_authors",
    "unique_authors",
    "unique_categories",
]

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
        try_change(
            session,
            insert(AuthorRow).values(
                id=author_id, name=author.name, surname=author.surname
            ),
        )
    except DBAPIError as e:
        # Committed by someone else since the lookup: use theirs. The
        # transaction is read committed (see add_book), so it sees that row.
        existing = _find_author(session, author)
        if not is_unique_violation(e) or existing is None:
            raise
        return existing
    return author_id


def insert_authors(
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


def replace_authors(
    session: Session, book_id: UUID, authors: tuple[Author, ...]
) -> None:
    """
    Make these the book's authors, in order. A former author that no book
    uses any more is deleted, so a misspelled name stops being suggested.
    """
    former = author_ids(session, book_id)
    # All at once: the rows are checked one by one, so they cannot be reordered in place.
    execute_change(
        session, delete(BookAuthorRow).where(BookAuthorRow.book_id == book_id)
    )
    insert_authors(session, book_id, authors)
    delete_unused_authors(session, former)


def author_ids(session: Session, book_id: UUID) -> list[UUID]:
    return list(
        session.scalars(
            select(BookAuthorRow.author_id).where(BookAuthorRow.book_id == book_id)
        )
    )


def delete_unused_authors(session: Session, author_ids: list[UUID]) -> list[UUID]:
    """Delete each of these authors that no book uses any more; the ones deleted."""
    deleted = []
    for author_id in author_ids:
        try:
            result = try_change(
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
            if not is_foreign_key_violation(e):
                raise
            logger.info(
                "Author %s kept: a book being saved at this moment uses it", author_id
            )
            continue
        if result.rowcount > 0:
            deleted.append(author_id)
    return deleted


def insert_categories(
    session: Session, book_id: UUID, categories: tuple[str, ...]
) -> None:
    for category in unique_categories(categories):
        execute_change(
            session,
            insert(BookCategoryRow).values(book_id=book_id, category=category),
        )
