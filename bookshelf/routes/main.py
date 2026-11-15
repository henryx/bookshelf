from uuid import UUID

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
from werkzeug.datastructures import MultiDict
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_db
from bookshelf.models.books import (
    SEARCH_MAX_LENGTH,
    AuthorField,
    Book,
    BookDetails,
    BookExistsError,
    IsbnAlreadySetError,
    Sort,
    UnknownBookError,
    add_book,
    find_book_by_isbn,
    find_book_details,
    list_books,
    suggest_authors,
    suggest_publishers,
    unique_tags,
    update_book,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import BookForm, EditBookForm

__all__ = ["list_state", "list_url", "main_route", "render_catalogue"]

main_route = Blueprint("main", __name__)

type ListState = tuple[str, Sort, int]


def _sort(value: str | None) -> Sort:
    """The requested order; title for a missing or unknown one."""
    try:
        return Sort(value)
    except ValueError:
        return Sort.TITLE


def list_state(values: MultiDict[str, str]) -> ListState:
    """A book list's search, order and page, from a query string or a form."""
    search = values.get("q", "").strip()
    page = values.get("page", 1, type=int) or 1
    return search, _sort(values.get("sort")), page


def list_url(endpoint: str, values: MultiDict[str, str]) -> str:
    """The URL of a book list (`endpoint`) showing the search, order and page in `values`."""
    search, sort, page = list_state(values)
    return url_for(
        endpoint,
        q=search or None,
        sort=None if sort is Sort.TITLE else sort.value,
        page=page if page > 1 else None,
    )


def render_catalogue(
    values: MultiDict[str, str], *, partial: bool, notice: str | None = None
) -> str:
    """
    The catalogue with the search, order and page in `values`: the list
    alone when `partial`, else the main page. `notice` is shown above it.
    """
    search, sort, page = list_state(values)
    return render_template(
        "_book_list.html" if partial else "main.html",
        books=list_books(get_db(), current_user.id, search, sort, page),
        search=search,
        sort=sort,
        sorts=Sort,
        search_max_length=SEARCH_MAX_LENGTH,
        list_endpoint="main.index",
        notice=notice,
        form=BookForm(),
    )


@main_route.route("/", methods=("GET",))
@login_required
def index() -> str:
    """The book list. htmx searches and page changes get only the list."""
    # Going back in history may refetch the page with htmx: it needs all of it.
    partial = is_htmx() and not request.headers.get("HX-History-Restore-Request")
    return render_catalogue(request.args, partial=partial)


def _render_add(form: BookForm, existing: Book | None = None) -> str:
    """
    The form alone for htmx (it lives in the main page's dialog), else a page.
    `existing`: the catalogue book whose ISBN was typed, to offer adding it
    to the user's collection instead.
    """
    template = "_add_book_form.html" if is_htmx() else "add_book.html"
    return render_template(template, form=form, existing=existing)


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
        return _render_add(form, find_book_by_isbn(get_db(), current_user.id, e.isbn))

    current_app.logger.info("Book %r added by %s", book.title, current_user.email)
    flash(f"Book “{book.title}” added.")
    return htmx_redirect(url_for("main.index"))


def _book_list_endpoint(values: MultiDict[str, str]) -> str:
    """The list a book was opened from: the collection or (by default) the catalogue."""
    return "collection.index" if values.get("list") == "collection" else "main.index"


def _render_book(
    form: EditBookForm, book: BookDetails, values: MultiDict[str, str]
) -> str:
    """
    Full page without htmx. With htmx, a GET fills the book dialog (details
    and form); a POST with errors swaps only the form. `values`: the list
    the book was opened from (which one, its search, order and page).
    """
    if not is_htmx():
        template = "book.html"
    elif request.method == "GET":
        template = "_book_details.html"
    else:
        template = "_edit_book_form.html"
    search, sort, page = list_state(values)
    endpoint = _book_list_endpoint(values)
    return render_template(
        template,
        form=form,
        book=book,
        list_name="collection" if endpoint == "collection.index" else "",
        search=search,
        sort=sort,
        page=page,
        back_url=list_url(endpoint, values),
    )


def _edited_fields(book: BookDetails, form: EditBookForm, isbn: str | None) -> str:
    """The fields a save changes, for the log."""
    fields = [
        name
        for name, old, new in (
            ("volume", book.volume, form.volume.data),
            ("notes", book.notes, form.notes.data),
            ("tags", sorted(book.tags), sorted(unique_tags(form.tag_names()))),
        )
        if old != new
    ]
    if isbn:
        fields.append("ISBN")
    return ", ".join(fields) or "no changes"


@main_route.route("/books/<uuid:book_id>", methods=("GET", "POST"))
@login_required
def book(book_id: UUID) -> BaseResponse | str:
    """
    A book's details, and saving its volume, notes and tags, and its ISBN
    while it has none. Any user may edit any book. Saving goes back to the
    list the book was opened from, as it was.
    """
    details = find_book_details(get_db(), book_id)
    if details is None:
        abort(404)
    form = EditBookForm(
        data={
            "volume": details.volume,
            "notes": details.notes,
            "tags": ", ".join(details.tags),
        }
    )
    # Once set, an ISBN is shown, not a field; a posted one is ignored.
    if details.isbn:
        del form.isbn
    values = request.form if request.method == "POST" else request.args
    if not form.validate_on_submit():
        return _render_book(form, details, values)

    isbn = form.isbn.data if "isbn" in form else None
    try:
        update_book(
            get_db(),
            book_id,
            volume=form.volume.data,
            notes=form.notes.data,
            tags=form.tag_names(),
            isbn=isbn,
        )
    except UnknownBookError:
        # Deleted since it was looked up.
        abort(404)
    except (BookExistsError, IsbnAlreadySetError) as e:
        form.form_errors.append(str(e))
        return _render_book(form, details, values)

    current_app.logger.info(
        "Book %r edited by %s: %s",
        details.title,
        current_user.email,
        _edited_fields(details, form, isbn),
    )
    flash(f"Book “{details.title}” saved.")
    return htmx_redirect(list_url(_book_list_endpoint(values), values))


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
