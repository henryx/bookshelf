"""The catalogue: every book in the library."""

from flask import render_template, request
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict

from bookshelf.db import get_session
from bookshelf.models.books import (
    PAGE_SIZE,
    PAGE_SIZES,
    SEARCH_MAX_LENGTH,
    Sort,
    list_books,
)
from bookshelf.routes import is_htmx
from bookshelf.routes.book_lists import author_filter, list_state
from bookshelf.routes.forms import BookForm
from bookshelf.routes.main import main_route

__all__ = ["render_catalogue"]


def render_catalogue(
    values: MultiDict[str, str], *, partial: bool, notice: str | None = None
) -> str:
    """
    The catalogue with the search, order, page and author (one author's
    books) in `values`: the list alone when `partial`, else the main page.
    `notice` is shown above it.
    """
    search, sort, page, size = list_state(values)
    author = author_filter(values)
    books = list_books(
        get_session(),
        current_user.id,
        search,
        sort,
        page,
        author_id=author.id if author else None,
        page_size=size,
    )
    return render_template(
        "_book_list.html" if partial else "main.html",
        books=books,
        author=author,
        search=search,
        sort=sort,
        sorts=Sort,
        size=size,
        page_sizes=PAGE_SIZES,
        default_size=PAGE_SIZE,
        search_max_length=SEARCH_MAX_LENGTH,
        list_endpoint="main.index",
        notice=notice,
        form=BookForm(),
    )


@main_route.route("/catalogue", methods=("GET",))
@login_required
def index() -> str:
    """The book list. htmx searches and page changes get only the list."""
    # Going back in history may refetch the page with htmx: it needs all of it.
    partial = is_htmx() and not request.headers.get("HX-History-Restore-Request")
    return render_catalogue(request.args, partial=partial)
