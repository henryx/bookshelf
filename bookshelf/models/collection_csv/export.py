"""Exporting a user's collection as CSV."""

import csv
import io
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from bookshelf.db.orm import (
    AuthorRow,
    BookAuthorRow,
    BookCategoryRow,
    BookRow,
    CollectionBookRow,
)
from bookshelf.models.books import Author
from bookshelf.models.collection_csv.columns import COLUMNS, SEPARATOR, guard

__all__ = ["export_collection"]


def export_collection(session: Session, user_id: UUID) -> str:
    """
    The user's collection as CSV text with COLUMNS, by title, starting with a
    byte order mark so that spreadsheets read it as UTF-8.
    """
    # The user's books, then the authors and categories of all of them.
    owned = CollectionBookRow.user_id == user_id
    books = session.execute(
        select(
            BookRow.id,
            BookRow.title,
            BookRow.isbn,
            BookRow.publisher,
            BookRow.publication_year,
            BookRow.series,
            BookRow.volume,
            BookRow.plot,
            CollectionBookRow.copies,
            CollectionBookRow.is_read,
            CollectionBookRow.score,
        )
        .join(BookRow, BookRow.id == CollectionBookRow.book_id)
        .where(owned)
        .order_by(BookRow.title, BookRow.id)
    ).all()
    authors: dict[UUID, list[str]] = {}
    for book_id, name, surname in session.execute(
        select(BookAuthorRow.book_id, AuthorRow.name, AuthorRow.surname)
        .select_from(CollectionBookRow)
        .join(BookAuthorRow, BookAuthorRow.book_id == CollectionBookRow.book_id)
        .join(AuthorRow, AuthorRow.id == BookAuthorRow.author_id)
        .where(owned)
        .order_by(BookAuthorRow.book_id, BookAuthorRow.sort_order)
    ):
        authors.setdefault(book_id, []).append(str(Author(name or "", surname)))
    categories: dict[UUID, list[str]] = {}
    for book_id, category in session.execute(
        select(BookCategoryRow.book_id, BookCategoryRow.category)
        .select_from(CollectionBookRow)
        .join(BookCategoryRow, BookCategoryRow.book_id == CollectionBookRow.book_id)
        .where(owned)
        .order_by(BookCategoryRow.book_id, BookCategoryRow.category)
    ):
        categories.setdefault(book_id, []).append(category)

    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for (
        book_id,
        title,
        isbn,
        publisher,
        year,
        series,
        volume,
        plot,
        copies,
        read,
        score,
    ) in books:
        values = (
            title,
            f"{SEPARATOR} ".join(authors.get(book_id, [])),
            isbn or "",
            publisher or "",
            "" if year is None else str(year),
            series or "",
            volume or "",
            plot or "",
            f"{SEPARATOR} ".join(categories.get(book_id, [])),
            str(copies),
            "yes" if read else "no",
            "" if score is None else str(score),
        )
        writer.writerow([guard(v) for v in values])
    return out.getvalue()
