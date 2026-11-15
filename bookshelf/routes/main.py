from collections.abc import Sequence
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
from bookshelf.models.authors import AuthorEntry, find_author
from bookshelf.models.books import (
    SEARCH_MAX_LENGTH,
    Author,
    AuthorField,
    Book,
    BookDetails,
    BookExistsError,
    BookField,
    BookInCollectionError,
    DeletedBook,
    IsbnAlreadySetError,
    Sort,
    UnknownBookError,
    add_book,
    delete_book,
    find_book,
    find_book_by_isbn,
    find_book_details,
    list_books,
    normalise_isbn,
    set_copies,
    suggest_authors,
    suggest_books,
    suggest_categories,
    suggest_publishers,
    unique_authors,
    unique_categories,
    update_book,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import BookForm, EditBookForm

__all__ = [
    "author_filter",
    "book_list_endpoint",
    "list_state",
    "list_url",
    "main_route",
    "render_catalogue",
]

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


def _author_param(values: MultiDict[str, str]) -> UUID | None:
    """The author a book list is limited to (`author`), if it is a valid ID."""
    try:
        return UUID(values.get("author", ""))
    except ValueError:
        return None


def author_filter(values: MultiDict[str, str]) -> AuthorEntry | None:
    """The author a book list is limited to, or None; 404 for an unknown one."""
    if not values.get("author"):
        return None
    author_id = _author_param(values)
    entry = find_author(get_db(), author_id) if author_id else None
    if entry is None:
        abort(404)
    return entry


def list_url(endpoint: str, values: MultiDict[str, str]) -> str:
    """
    The URL of a book list (`endpoint`) showing the search, order, page and
    author in `values`.
    """
    search, sort, page = list_state(values)
    return url_for(
        endpoint,
        author=_author_param(values),
        q=search or None,
        sort=None if sort is Sort.TITLE else sort.value,
        page=page if page > 1 else None,
    )


def render_catalogue(
    values: MultiDict[str, str], *, partial: bool, notice: str | None = None
) -> str:
    """
    The catalogue with the search, order, page and author (one author's
    books) in `values`: the list alone when `partial`, else the main page.
    `notice` is shown above it.
    """
    search, sort, page = list_state(values)
    author = author_filter(values)
    books = list_books(
        get_db(),
        current_user.id,
        search,
        sort,
        page,
        author_id=author.id if author else None,
    )
    return render_template(
        "_book_list.html" if partial else "main.html",
        books=books,
        author=author,
        search=search,
        sort=sort,
        sorts=Sort,
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


def _add_list(values: MultiDict[str, str]) -> str:
    """The list the add-book form was opened from: "collection", or "" for the catalogue."""
    return "collection" if book_list_endpoint(values) == "collection.index" else ""


def _render_add(
    form: BookForm, existing: Book | None = None, picked: Book | None = None
) -> str:
    """
    The form alone for htmx (it lives in the dialog of the catalogue and of
    the collection), else a page.
    `existing`: the catalogue book whose ISBN was typed, to offer adding it
    to the user's collection instead.
    `picked`: the catalogue book picked from the title or ISBN suggestions;
    the form shows it read-only and adds it to the user's collection.
    """
    template = "_add_book_form.html" if is_htmx() else "add_book.html"
    return render_template(
        template,
        form=form,
        existing=existing,
        picked=picked,
        add_list=_add_list(request.values),
    )


def _book_data(details: BookDetails) -> dict[str, object]:
    """A book's fields as form data, for the edit form and a picked book's add form."""
    return {
        "authors": [{"name": a.name, "surname": a.surname} for a in details.authors],
        "publisher": details.publisher,
        "publication_year": details.publication_year,
        "series": details.series,
        "volume": details.volume,
        "plot": details.plot,
        "categories": list(details.categories) or [""],
        "isbn": details.isbn,
    }


def _render_picked(text: str) -> str:
    """The add form filled, read-only, with the catalogue book `text` (its id)."""
    try:
        book_id = UUID(text)
    except ValueError:
        abort(404)
    details = find_book_details(get_db(), book_id)
    book = find_book(get_db(), current_user.id, book_id)
    if details is None or book is None:
        abort(404)
    form = BookForm(formdata=None, data={**_book_data(details), "title": details.title})
    return _render_add(form, picked=book)


def _change_rows(form: EditBookForm) -> bool:
    """
    Without JavaScript the + and - buttons of the author and category rows
    submit the form (add_author, remove_author=<index>, and the same for
    category): add or remove that row. True when one did, so the form is
    shown again without saving.
    """
    for field, row in ((form.authors, "author"), (form.categories, "category")):
        if f"add_{row}" in request.form:
            form.add_row(field)
            return True
        if remove := request.form.get(f"remove_{row}"):
            form.remove_row(field, remove)
            return True
    return False


@main_route.route("/books/new", methods=("GET", "POST"))
@login_required
def new_book() -> BaseResponse | str:
    """
    Add a book to the catalogue and to the current user's collection. A GET
    with `book` (picked from the title or ISBN suggestions) shows the form
    for adding that catalogue book to the collection instead.
    """
    if request.method == "GET" and "book" in request.args:
        return _render_picked(request.args["book"])
    form = BookForm()
    if _change_rows(form):
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
    return htmx_redirect(url_for(book_list_endpoint(request.form)))


def book_list_endpoint(values: MultiDict[str, str]) -> str:
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
    endpoint = book_list_endpoint(values)
    return render_template(
        template,
        form=form,
        book=book,
        list_name="collection" if endpoint == "collection.index" else "",
        author_id=_author_param(values),
        search=search,
        sort=sort,
        page=page,
        back_url=list_url(endpoint, values),
    )


def _isbn_change(book: BookDetails, form: EditBookForm) -> tuple[str | None, bool]:
    """
    The ISBN to save (ISBN-13), and whether it replaces (or, when None,
    removes) the book's own: only admins have the field once the book has an
    ISBN, and an unchanged one is not saved again.
    """
    if "isbn" not in form:
        return None, False
    isbn = normalise_isbn(form.isbn.data) if form.isbn.data else None
    if book.isbn is None:
        return isbn, False
    if isbn == book.isbn:
        return None, False
    return isbn, True


def _author_keys(authors: Sequence[Author]) -> list[tuple[str, str]]:
    """Authors as compared when picked: ignoring case."""
    return [(a.name.casefold(), a.surname.casefold()) for a in authors]


def _edited_fields(
    book: BookDetails, form: EditBookForm, isbn: str | None, replace_isbn: bool
) -> str:
    """The fields a save changes, for the log; a corrected ISBN with both values."""
    fields = [
        name
        for name, old, new in (
            (
                "authors",
                _author_keys(book.authors),
                _author_keys(unique_authors(form.author_list())),
            ),
            ("publisher", book.publisher, form.publisher.data),
            ("year", book.publication_year, form.publication_year.data),
            ("series", book.series, form.series.data),
            ("volume", book.volume, form.volume.data),
            ("plot", book.plot, form.plot.data),
            (
                "categories",
                sorted(book.categories),
                sorted(unique_categories(form.category_names())),
            ),
        )
        if old != new
    ]
    if replace_isbn:
        fields.append(f"ISBN {book.isbn} -> {isbn or 'none'}")
    elif isbn:
        fields.append("ISBN")
    return ", ".join(fields) or "no changes"


@main_route.route("/books/<uuid:book_id>", methods=("GET", "POST"))
@login_required
def book(book_id: UUID) -> BaseResponse | str:
    """
    A book's details, and saving every field but the title, and its ISBN
    while it has none, and the copies the user owns while it is in their
    collection. Any user may edit any book; admins may also correct or
    remove an ISBN already set. Saving goes back to the list the book was
    opened from, as it was.
    """
    details = find_book_details(get_db(), book_id)
    if details is None:
        abort(404)
    owned = find_book(get_db(), current_user.id, book_id)
    copies = owned.copies if owned else None
    form = EditBookForm(data={**_book_data(details), "copies": copies})
    # Once set, an ISBN is shown, not a field, except to admins; a posted one
    # is ignored.
    if details.isbn and not current_user.is_admin:
        del form.isbn
    if copies is None:
        del form.copies
    values = request.form if request.method == "POST" else request.args
    if _change_rows(form):
        return _render_book(form, details, values)
    if not form.validate_on_submit():
        return _render_book(form, details, values)

    isbn, replace_isbn = _isbn_change(details, form)
    try:
        update_book(
            get_db(),
            book_id,
            authors=form.author_list(),
            publisher=form.publisher.data,
            publication_year=form.publication_year.data,
            series=form.series.data,
            volume=form.volume.data,
            plot=form.plot.data,
            categories=form.category_names(),
            isbn=isbn,
            replace_isbn=replace_isbn,
        )
    except UnknownBookError:
        # Deleted since it was looked up.
        current_app.logger.info(
            "Book %s deleted before %s saved it", book_id, current_user.email
        )
        abort(404)
    except (BookExistsError, IsbnAlreadySetError) as e:
        form.form_errors.append(str(e))
        return _render_book(form, details, values)
    _save_copies(form, details, copies)

    current_app.logger.info(
        "Book %r edited by %s: %s",
        details.title,
        current_user.email,
        _edited_fields(details, form, isbn, replace_isbn),
    )
    flash(f"Book “{details.title}” saved.")
    return htmx_redirect(list_url(book_list_endpoint(values), values))


def _save_copies(form: EditBookForm, book: BookDetails, owned: int | None) -> None:
    """
    The copies posted in the edit form, when the book is in the user's
    collection (`owned` copies) and they are given and changed.
    """
    copies = form.copies.data if "copies" in form else None
    if copies is None or copies == owned:
        return
    if set_copies(get_db(), current_user.id, book.id, copies):
        current_app.logger.info(
            "Copies of %r set to %d by %s", book.title, copies, current_user.email
        )
        return
    # Removed from the collection since it was looked up.
    current_app.logger.info(
        "Book %r left the collection of %s before its copies were saved",
        book.title,
        current_user.email,
    )


def _list_after_delete(values: MultiDict[str, str], deleted: DeletedBook) -> str:
    """
    The list a book was deleted from, as it was; without its author filter
    when that author went with the book (they had no other book).
    """
    if _author_param(values) not in deleted.authors_deleted:
        return list_url(book_list_endpoint(values), values)
    others = values.copy()
    others.pop("author")
    return list_url(book_list_endpoint(others), others)


@main_route.post("/books/<uuid:book_id>/delete")
@login_required
def delete(book_id: UUID) -> BaseResponse:
    """
    Delete a book for good: admins only, and only while it is in nobody's
    collection. Goes back to the list it was deleted from, as it was.
    """
    if not current_user.is_admin:
        abort(403)
    try:
        deleted = delete_book(get_db(), book_id)
    except UnknownBookError:
        current_app.logger.info(
            "Delete of unknown book %s by %s", book_id, current_user.email
        )
        abort(404)
    except BookInCollectionError as e:
        # Added to a collection since the dialog was opened.
        current_app.logger.info(
            "Delete of book %s by %s refused: it is in a collection",
            book_id,
            current_user.email,
        )
        abort(409, description=str(e))
    current_app.logger.info("Book %r deleted by %s", deleted.title, current_user.email)
    flash(f"Book “{deleted.title}” deleted.")
    return htmx_redirect(_list_after_delete(request.form, deleted))


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


@main_route.route("/books/suggestions", methods=("GET",))
@login_required
def book_suggestions() -> str:
    """
    <option>s for the add-book form's title or ISBN <datalist> (by
    `field`): catalogue books whose title or ISBN starts with `q`. Picking
    one loads it into the form (app.js).
    """
    try:
        field = BookField(request.args.get("field"))
    except ValueError:
        abort(400, description="Unknown book field.")
    books = suggest_books(get_db(), request.args.get("q", ""), field)
    return render_template("_book_suggestions.html", books=books, field=field)


@main_route.route("/publishers/suggestions", methods=("GET",))
@login_required
def publisher_suggestions() -> str:
    """<option>s for the publisher's <datalist>: publishers starting with `q`."""
    publishers = suggest_publishers(get_db(), request.args.get("q", ""))
    return render_template("_publisher_suggestions.html", publishers=publishers)


@main_route.route("/categories/suggestions", methods=("GET",))
@login_required
def category_suggestions() -> str:
    """<option>s for a category row's <datalist>: categories starting with `q`."""
    categories = suggest_categories(get_db(), request.args.get("q", ""))
    return render_template("_category_suggestions.html", categories=categories)
