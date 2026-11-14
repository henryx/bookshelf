from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_db
from bookshelf.models.books import (
    SEARCH_MAX_LENGTH,
    AuthorField,
    BookExistsError,
    Sort,
    add_book,
    list_books,
    suggest_authors,
    suggest_publishers,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import BookForm

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
        form=BookForm(),
    )


def _render_add(form: BookForm) -> str:
    """The form alone for htmx (it lives in the main page's dialog), else a page."""
    template = "_add_book_form.html" if is_htmx() else "add_book.html"
    return render_template(template, form=form)


@main_route.route("/books/new", methods=("GET", "POST"))
@login_required
def new_book() -> BaseResponse | str:
    """Add a book to the catalogue and to the current user's collection."""
    form = BookForm()
    # Without JavaScript the + and - buttons of the author rows submit the
    # form: show it again with the row added or removed, without saving.
    if "add_author" in request.form:
        form.add_author_row()
        return _render_add(form)
    if remove := request.form.get("remove_author"):
        form.remove_author_row(remove)
        return _render_add(form)
    if not form.validate_on_submit():
        return _render_add(form)
    book = form.to_book()
    try:
        add_book(get_db(), current_user.id, book, copies=form.copies.data or 1)
    except BookExistsError as e:
        form.form_errors.append(str(e))
        return _render_add(form)

    current_app.logger.info("Book %r added by %s", book.title, current_user.email)
    flash(f"Book “{book.title}” added.")
    return htmx_redirect(url_for("main.index"))


@main_route.route("/authors/suggestions", methods=("GET",))
@login_required
def author_suggestions() -> str:
    """
    <option>s for an author row's <datalist>: authors whose name (or
    surname, by `field`) starts with `q`.
    """
    try:
        field = AuthorField(request.args.get("field"))
    except ValueError:
        abort(400, description="Unknown author field.")
    authors = suggest_authors(get_db(), request.args.get("q", ""), field)
    return render_template("_author_suggestions.html", authors=authors)


@main_route.route("/publishers/suggestions", methods=("GET",))
@login_required
def publisher_suggestions() -> str:
    """<option>s for the publisher's <datalist>: publishers starting with `q`."""
    publishers = suggest_publishers(get_db(), request.args.get("q", ""))
    return render_template("_publisher_suggestions.html", publishers=publishers)
