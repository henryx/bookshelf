"""Adding a book: to the catalogue and the collection, or a picked catalogue book to the collection."""

from uuid import UUID

from flask import abort, current_app, flash, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_session
from bookshelf.models.books import (
    Book,
    BookExistsError,
    BookLookupError,
    InvalidIsbnError,
    add_book,
    code_label,
    find_book,
    find_book_by_isbn,
    find_book_details,
    look_up_isbn,
    normalise_isbn,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.book_lists import book_list_endpoint
from bookshelf.routes.forms import BookForm
from bookshelf.routes.main import main_route
from bookshelf.routes.main.book_form import book_data, change_rows


def _add_list(values: MultiDict[str, str]) -> str:
    """The list the add-book form was opened from: "collection", or "" for the catalogue."""
    return "collection" if book_list_endpoint(values) == "collection.index" else ""


def _render_add(
    form: BookForm,
    existing: Book | None = None,
    picked: Book | None = None,
    notice: str | None = None,
) -> str:
    """
    The form alone for htmx (it lives in the dialog of the catalogue and of
    the collection), else a page.
    `existing`: the catalogue book whose ISBN was typed, to offer adding it
    to the user's collection instead.
    `picked`: the catalogue book picked from the title or ISBN suggestions;
    the form shows it read-only and adds it to the user's collection.
    `notice`: what the Look up button filled in.
    """
    template = "_add_book_form.html" if is_htmx() else "add_book.html"
    return render_template(
        template,
        form=form,
        existing=existing,
        picked=picked,
        notice=notice,
        add_list=_add_list(request.values),
    )


def _render_picked(text: str) -> str:
    """The add form filled, read-only, with the catalogue book `text` (its id)."""
    try:
        book_id = UUID(text)
    except ValueError:
        abort(404)
    details = find_book_details(get_session(), book_id)
    book = find_book(get_session(), current_user.id, book_id)
    if details is None or book is None:
        abort(404)
    form = BookForm(formdata=None, data={**book_data(details), "title": details.title})
    return _render_add(form, picked=book)


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
    if change_rows(form):
        return _render_add(form)
    if "lookup" in request.form:
        return _look_up(form)
    if not form.validate_on_submit():
        return _render_add(form)
    book = form.to_book()
    try:
        add_book(get_session(), current_user.id, book, copies=form.copies.data or 1)
    except BookExistsError as e:
        form.form_errors.append(str(e))
        return _render_add(
            form, find_book_by_isbn(get_session(), current_user.id, e.isbn)
        )

    current_app.logger.info("Book %r added by %s", book.title, current_user.email)
    flash(f"Book “{book.title}” added.")
    return htmx_redirect(url_for(book_list_endpoint(request.form)))


def _look_up(form: BookForm) -> str:
    """
    The Look up button: fill the form's empty fields with the book its ISBN
    finds online (nothing is saved), or offer the catalogue's book with it.
    """
    try:
        isbn = normalise_isbn(form.isbn.data or "")
    except InvalidIsbnError:
        form.form_errors.append("Enter a valid ISBN to look it up.")
        return _render_add(form)
    if existing := find_book_by_isbn(get_session(), current_user.id, isbn):
        return _render_add(form, existing)
    if code_label(isbn) == "ISSN":
        # Google Books and Open Library hardly know serials.
        form.form_errors.append(
            "Look up finds books by their ISBN: fill in a serial's fields by hand."
        )
        return _render_add(form)
    try:
        found = look_up_isbn(
            isbn, google_books_key=current_app.config["GOOGLE_BOOKS_API_KEY"]
        )
    except BookLookupError as e:
        form.form_errors.append(str(e))
        return _render_add(form)
    if found is None:
        current_app.logger.info(
            "ISBN %s looked up by %s: not found", isbn, current_user.email
        )
        form.form_errors.append(f"No book found online with ISBN {form.isbn.data}.")
        return _render_add(form)
    current_app.logger.info(
        "ISBN %s looked up by %s: found in %s", isbn, current_user.email, found.source
    )
    return _render_add(
        form, notice=_filled_notice(form.fill_empty(found), found.source)
    )


def _filled_notice(filled: list[str], source: str) -> str:
    """What the Look up button filled in: "Filled in title and year from …"."""
    if not filled:
        return f"{source} has this book, but nothing to add to it."
    fields = (
        filled[0] if len(filled) == 1 else f"{', '.join(filled[:-1])} and {filled[-1]}"
    )
    return f"Filled in {fields} from {source}."
