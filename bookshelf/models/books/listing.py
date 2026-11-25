"""The catalogue and collection lists: a page of books, searched and sorted."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from statistics import median
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Select,
    String,
    and_,
    func,
    literal,
    select,
)
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import Label, UnaryExpression
from sqlalchemy.sql.selectable import ScalarSelect

from bookshelf.db.orm import (
    AuthorRow,
    BookAuthorRow,
    BookRow,
    CollectionBookRow,
    UUIDType,
)
from bookshelf.models.books.finding import books_from_rows
from bookshelf.models.books.isbn import isbn_searches
from bookshelf.models.books.model import (
    PAGE_SIZE,
    SEARCH_MAX_LENGTH,
    Book,
    BookPage,
    Sort,
)

# A list page's query: BookListRow's columns, then the total. The author's
# columns are None for a book without authors (an outer join), which their
# types cannot tell.
type _PageQuery = Select[
    UUID, str, int | None, int | None, bool | None, int | None, str, str, UUID, int
]
# A column a list is sorted by, and one in its order.
type _SortColumn = (
    ColumnElement[str]
    | ColumnElement[str | None]
    | ColumnElement[datetime]
    | ColumnElement[UUID]
)
type _SortKey = (
    _SortColumn
    | UnaryExpression[str]
    | UnaryExpression[str | None]
    | UnaryExpression[datetime]
    | UnaryExpression[UUID]
)


def _matches(search: str, author_id: UUID | None) -> ColumnElement[bool]:
    """
    The search: title, any author's "name surname" or ISBN contains the
    text, ignoring case, and, with `author_id`, only that author's books. The
    BOOK_MATCHES function (db/schema/functions.py) holds the condition.
    """
    isbn, isbn_13 = isbn_searches(search)
    return func.book_matches(
        BookRow.id,
        BookRow.title,
        BookRow.isbn,
        literal(search, String(SEARCH_MAX_LENGTH)),
        literal(isbn, String(SEARCH_MAX_LENGTH)),
        literal(isbn_13, String(SEARCH_MAX_LENGTH)),
        literal(author_id, UUIDType),
        type_=Boolean,
    )


def _first_author(column: InstrumentedAttribute[str]) -> ScalarSelect[str]:
    """The book's first author's `column` (NAME or SURNAME); None without authors."""
    return (
        select(column)
        .select_from(BookAuthorRow)
        .join(AuthorRow, AuthorRow.id == BookAuthorRow.author_id)
        .where(BookAuthorRow.book_id == BookRow.id)
        .order_by(BookAuthorRow.sort_order)
        .limit(1)
        .scalar_subquery()
    )


def _sort_columns(sort: Sort) -> list[Label[str] | Label[datetime]]:
    """The columns a page of books adds for `sort`, labelled."""
    match sort:
        case Sort.TITLE:
            return []
        case Sort.AUTHOR:
            return [
                _first_author(AuthorRow.surname).label("first_surname"),
                _first_author(AuthorRow.name).label("first_name"),
            ]
        case Sort.ADDED:
            return [BookRow.created_at.label("created_at")]


def _order(sort: Sort, column: Callable[[str], _SortColumn]) -> list[_SortKey]:
    """The order of `sort`, on the columns `column` gives by name."""
    match sort:
        case Sort.TITLE:
            return [column("title"), column("id")]
        case Sort.AUTHOR:
            # By first author's surname, then name; books without authors last.
            return [
                column("first_surname").nulls_last(),
                column("first_name"),
                column("title"),
                column("id"),
            ]
        case Sort.ADDED:
            # Newest first.
            return [column("created_at").desc(), column("id")]


def _list_query(
    user_id: UUID,
    search: str,
    sort: Sort,
    author_id: UUID | None,
    *,
    collection: bool,
) -> Callable[[int, int], _PageQuery]:
    """
    The query for one page of books at an offset: the page of books (a CTE,
    p), then a row per author, in the page's order and each book's author order, with
    the current user's copies and, last, the number of books matching the
    search on every page (TOTAL), so the one scan the search needs also
    counts them. With `collection`, only the books in the user's collection.
    """

    def query(offset: int, page_size: int) -> _PageQuery:
        sort_columns = {column.name: column for column in _sort_columns(sort)}
        page_query = select(
            BookRow.id,
            BookRow.title,
            BookRow.publication_year,
            func.count().over().label("total"),
            *sort_columns.values(),
        ).where(_matches(search, author_id))
        if collection:
            page_query = (
                page_query.add_columns(
                    CollectionBookRow.copies,
                    CollectionBookRow.is_read,
                    CollectionBookRow.score,
                )
                .join(CollectionBookRow, CollectionBookRow.book_id == BookRow.id)
                .where(CollectionBookRow.user_id == user_id)
            )
        page = (
            page_query.order_by(
                *_order(
                    sort,
                    lambda name: (
                        sort_columns[name]
                        if name in sort_columns
                        else getattr(BookRow, name)
                    ),
                )
            )
            .offset(offset)
            .limit(page_size)
            .cte("p")
        )
        copies, is_read, score = (
            (page.c.copies, page.c.is_read, page.c.score)
            if collection
            else (
                CollectionBookRow.copies,
                CollectionBookRow.is_read,
                CollectionBookRow.score,
            )
        )
        rows = select(
            page.c.id,
            page.c.title,
            page.c.publication_year,
            copies,
            is_read,
            score,
            AuthorRow.name,
            AuthorRow.surname,
            AuthorRow.id,
            page.c.total,
        ).select_from(page)
        if not collection:
            rows = rows.outerjoin(
                CollectionBookRow,
                and_(
                    CollectionBookRow.book_id == page.c.id,
                    CollectionBookRow.user_id == user_id,
                ),
            )
        return (
            rows.outerjoin(BookAuthorRow, BookAuthorRow.book_id == page.c.id)
            .outerjoin(AuthorRow, AuthorRow.id == BookAuthorRow.author_id)
            .order_by(
                *_order(sort, lambda name: page.c[name]), BookAuthorRow.sort_order
            )
        )

    return query


def _count_query(
    user_id: UUID, search: str, author_id: UUID | None, *, collection: bool
) -> Select[int]:
    """The number of books a list's search matches: only for a page past the last."""
    query = select(func.count()).select_from(BookRow).where(_matches(search, author_id))
    if collection:
        query = query.join(
            CollectionBookRow, CollectionBookRow.book_id == BookRow.id
        ).where(CollectionBookRow.user_id == user_id)
    return query


def _book_page(
    session: Session,
    list_query: Callable[[int, int], _PageQuery],
    count_query: Select[int],
    page: int,
    page_size: int,
) -> BookPage:
    """
    One page of books from `list_query` (the query for an offset), whose rows
    end with the total. A page out of range gives the nearest existing page;
    only then is `count_query` run, to find it.
    """
    page = max(page, 1)
    rows = session.execute(list_query((page - 1) * page_size, page_size)).all()
    if not rows and page > 1:
        total = session.scalar(count_query) or 0
        page = BookPage([], total, 1, page_size).pages
        rows = session.execute(list_query((page - 1) * page_size, page_size)).all()
    total = rows[0][-1] if rows else 0
    return BookPage(books_from_rows([row[:-1] for row in rows]), total, page, page_size)


def _with_medians(session: Session, books: list[Book]) -> list[Book]:
    """The books with the median of every user's score of each, and their number."""
    scores: dict[UUID, list[int]] = {}
    for book_id, score in session.execute(
        select(CollectionBookRow.book_id, CollectionBookRow.score).where(
            CollectionBookRow.book_id.in_([book.id for book in books])
        )
    ):
        if score is not None:
            scores.setdefault(book_id, []).append(score)
    return [
        replace(book, median_score=median(found), scores=len(found))
        if (found := scores.get(book.id))
        else book
        for book in books
    ]


def list_books(
    session: Session,
    user_id: UUID,
    search: str = "",
    sort: Sort = Sort.TITLE,
    page: int = 1,
    *,
    author_id: UUID | None = None,
    page_size: int = PAGE_SIZE,
) -> BookPage:
    """
    One page of the catalogue, filtered by `search` (title, author or ISBN,
    ignoring case) and, when given, to the books of the author `author_id`,
    with the copies `user_id` owns of each book. A page out of range gives
    the nearest existing page. Each book has the median of every user's
    score of it.
    """
    books = _list(session, user_id, search, sort, page, author_id, page_size, False)
    if not books.books:
        return books
    return replace(books, books=_with_medians(session, books.books))


def list_collection(
    session: Session,
    user_id: UUID,
    search: str = "",
    sort: Sort = Sort.TITLE,
    page: int = 1,
    *,
    author_id: UUID | None = None,
    page_size: int = PAGE_SIZE,
) -> BookPage:
    """
    One page of the books in `user_id`'s collection, as list_books. "Date
    added" is the date the book entered the catalogue: collections do not
    record when a book joined.
    """
    return _list(session, user_id, search, sort, page, author_id, page_size, True)


def _list(
    session: Session,
    user_id: UUID,
    search: str,
    sort: Sort,
    page: int,
    author_id: UUID | None,
    page_size: int,
    collection: bool,
) -> BookPage:
    search = search.strip()
    if len(search) > SEARCH_MAX_LENGTH:
        return BookPage([], 0, 1, page_size)
    return _book_page(
        session,
        _list_query(user_id, search, sort, author_id, collection=collection),
        _count_query(user_id, search, author_id, collection=collection),
        page,
        page_size,
    )
