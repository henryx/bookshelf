"""A book list's state (search, order, page, size, author), shared by the catalogue and the collection."""

from uuid import UUID

from flask import abort, url_for
from werkzeug.datastructures import MultiDict

from bookshelf.db import get_session
from bookshelf.models.authors import AuthorEntry, find_author
from bookshelf.models.books import PAGE_SIZE, Sort
from bookshelf.routes import page_size

__all__ = [
    "ListState",
    "author_filter",
    "author_param",
    "book_list_endpoint",
    "list_state",
    "list_url",
]

# A book list's search, order, page and page size.
type ListState = tuple[str, Sort, int, int]


def _sort(value: str | None) -> Sort:
    """The requested order; title for a missing or unknown one."""
    try:
        return Sort(value)
    except ValueError:
        return Sort.TITLE


def list_state(values: MultiDict[str, str]) -> ListState:
    """A book list's search, order, page and page size, from a query string or a form."""
    search = values.get("q", "").strip()
    page = values.get("page", 1, type=int) or 1
    return search, _sort(values.get("sort")), page, page_size(values)


def author_param(values: MultiDict[str, str]) -> UUID | None:
    """The author a book list is limited to (`author`), if it is a valid ID."""
    try:
        return UUID(values.get("author", ""))
    except ValueError:
        return None


def author_filter(values: MultiDict[str, str]) -> AuthorEntry | None:
    """The author a book list is limited to, or None; 404 for an unknown one."""
    if not values.get("author"):
        return None
    author_id = author_param(values)
    entry = find_author(get_session(), author_id) if author_id else None
    if entry is None:
        abort(404)
    return entry


def list_url(endpoint: str, values: MultiDict[str, str]) -> str:
    """
    The URL of a book list (`endpoint`) showing the search, order, page,
    page size and author in `values`.
    """
    search, sort, page, size = list_state(values)
    return url_for(
        endpoint,
        author=author_param(values),
        q=search or None,
        sort=None if sort is Sort.TITLE else sort.value,
        page=page if page > 1 else None,
        size=None if size == PAGE_SIZE else size,
    )


def book_list_endpoint(values: MultiDict[str, str]) -> str:
    """The list a book was opened from: the collection or (by default) the catalogue."""
    return "collection.index" if values.get("list") == "collection" else "main.index"
