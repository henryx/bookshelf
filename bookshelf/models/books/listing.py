"""The catalogue and collection lists, and finding one book."""

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
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
from sqlalchemy.orm import InstrumentedAttribute, Session, selectinload
from sqlalchemy.sql.elements import Label, UnaryExpression
from sqlalchemy.sql.selectable import ScalarSelect

from bookshelf.db.orm import (
    AuthorRow,
    BookAuthorRow,
    BookRow,
    CollectionBookRow,
    UUIDType,
)
from bookshelf.models.books.isbn import isbn_searches
from bookshelf.models.books.model import (
    PAGE_SIZE,
    SEARCH_MAX_LENGTH,
    Author,
    Book,
    BookDetails,
    BookPage,
    Sort,
)

# A book list's row: ID, TITLE, PUBLICATION_YEAR, the user's COPIES and
# IS_READ, then one author's NAME, SURNAME, ID (all None for a book without
# authors).
type _BookRow = tuple[
    UUID, str, int | None, int | None, bool | None, str | None, str | None, UUID | None
]
# A list page's query: _BookRow's columns, then the total. The author's
# columns are None for a book without authors (an outer join), which their
# types cannot tell.
type _PageQuery = Select[
    UUID, str, int | None, int | None, bool | None, str, str, UUID, int
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
    BOOK_MATCHES function (db/schema.py) holds the condition.
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
    The query for one page of books at an offset: a page of books, then a
    row per author, in the page's order and each book's author order, with
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
                    CollectionBookRow.copies, CollectionBookRow.is_read
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
            .subquery("p")
        )
        copies, is_read = (
            (page.c.copies, page.c.is_read)
            if collection
            else (CollectionBookRow.copies, CollectionBookRow.is_read)
        )
        rows = select(
            page.c.id,
            page.c.title,
            page.c.publication_year,
            copies,
            is_read,
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


def _books(rows: Iterable[_BookRow]) -> list[Book]:
    """Group the per-author rows into books, keeping their order."""
    books: dict[UUID, Book] = {}
    for book_id, title, year, copies, read, name, surname, author_id in rows:
        book = books.get(book_id) or Book(
            book_id, title, (), year, copies, read=bool(read)
        )
        if surname is not None and author_id is not None:
            author = Author(name or "", surname, id=author_id)
            book = replace(book, authors=(*book.authors, author))
        books[book_id] = book
    return list(books.values())


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
    return BookPage(_books([row[:-1] for row in rows]), total, page, page_size)


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
    the nearest existing page.
    """
    return _list(session, user_id, search, sort, page, author_id, page_size, False)


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


def _one_book(
    session: Session, user_id: UUID, condition: ColumnElement[bool]
) -> Book | None:
    """The book matching `condition`, a row per author as in the lists."""
    rows = session.execute(
        select(
            BookRow.id,
            BookRow.title,
            BookRow.publication_year,
            CollectionBookRow.copies,
            CollectionBookRow.is_read,
            AuthorRow.name,
            AuthorRow.surname,
            AuthorRow.id,
        )
        .outerjoin(
            CollectionBookRow,
            and_(
                CollectionBookRow.book_id == BookRow.id,
                CollectionBookRow.user_id == user_id,
            ),
        )
        .outerjoin(BookAuthorRow, BookAuthorRow.book_id == BookRow.id)
        .outerjoin(AuthorRow, AuthorRow.id == BookAuthorRow.author_id)
        .where(condition)
        .order_by(BookAuthorRow.sort_order)
    )
    books = _books(rows)
    return books[0] if books else None


def find_book(session: Session, user_id: UUID, book_id: UUID) -> Book | None:
    """The catalogue book `book_id`, with the copies `user_id` owns; None if unknown."""
    return _one_book(session, user_id, BookRow.id == book_id)


def find_book_details(session: Session, book_id: UUID) -> BookDetails | None:
    """Every field of the catalogue book `book_id`; None if unknown."""
    book = session.scalars(
        select(BookRow)
        .where(BookRow.id == book_id)
        .options(selectinload(BookRow.authors), selectinload(BookRow.categories))
    ).one_or_none()
    if book is None:
        return None
    collections = session.scalar(
        select(func.count()).where(CollectionBookRow.book_id == book_id)
    )
    return BookDetails(
        id=book_id,
        title=book.title,
        authors=tuple(Author(ba.author.name, ba.author.surname) for ba in book.authors),
        isbn=book.isbn,
        publisher=book.publisher,
        publication_year=book.publication_year,
        series=book.series,
        volume=book.volume,
        plot=book.plot,
        categories=tuple(category.category for category in book.categories),
        collections=collections or 0,
    )


def find_book_by_isbn(session: Session, user_id: UUID, isbn: str) -> Book | None:
    """The catalogue book with this ISBN-13, as find_book; None if there is none."""
    return _one_book(session, user_id, BookRow.isbn == isbn)
