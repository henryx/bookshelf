from flask import Blueprint, render_template, request
from flask_login import login_required

from bookshelf.db import get_db
from bookshelf.models.books import SEARCH_MAX_LENGTH, Sort, list_books
from bookshelf.routes import is_htmx

__all__ = ["main_route"]

main_route = Blueprint("main", __name__)


def _sort(value: str | None) -> Sort:
    """The requested order; title for a missing or unknown one."""
    try:
        return Sort(value)
    except ValueError:
        return Sort.TITLE


@main_route.route("/", methods=("GET",))
@login_required
def index() -> str:
    """The book list. htmx searches and page changes get only the list."""
    search = request.args.get("q", "").strip()
    sort = _sort(request.args.get("sort"))
    page = request.args.get("page", 1, type=int)
    books = list_books(get_db(), search, sort, page)
    # Going back in history may refetch the page with htmx: it needs all of it.
    partial = is_htmx() and not request.headers.get("HX-History-Restore-Request")
    return render_template(
        "_book_list.html" if partial else "main.html",
        books=books,
        search=search,
        sort=sort,
        sorts=Sort,
        search_max_length=SEARCH_MAX_LENGTH,
    )
