"""A book's details: editing it (and the user's copies), and deleting it (admins only)."""

from collections.abc import Sequence
from uuid import UUID

from flask import abort, current_app, flash, render_template, request
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_session
from bookshelf.models.books import (
    Author,
    BookDetails,
    BookExistsError,
    BookInCollectionError,
    DeletedBook,
    IsbnAlreadySetError,
    UnknownBookError,
    delete_book,
    find_book,
    find_book_details,
    normalise_isbn,
    set_copies,
    unique_authors,
    unique_categories,
    update_book,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.book_lists import (
    author_param,
    book_list_endpoint,
    list_state,
    list_url,
)
from bookshelf.routes.forms import EditBookForm
from bookshelf.routes.main import main_route
from bookshelf.routes.main.book_form import book_data, change_rows


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
    search, sort, page, size = list_state(values)
    endpoint = book_list_endpoint(values)
    return render_template(
        template,
        form=form,
        book=book,
        list_name="collection" if endpoint == "collection.index" else "",
        author_id=author_param(values),
        search=search,
        sort=sort,
        page=page,
        size=size,
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
    details = find_book_details(get_session(), book_id)
    if details is None:
        abort(404)
    owned = find_book(get_session(), current_user.id, book_id)
    copies = owned.copies if owned else None
    form = EditBookForm(data={**book_data(details), "copies": copies})
    # Once set, an ISBN is shown, not a field, except to admins; a posted one
    # is ignored.
    if details.isbn and not current_user.is_admin:
        del form.isbn
    if copies is None:
        del form.copies
    values = request.form if request.method == "POST" else request.args
    if change_rows(form):
        return _render_book(form, details, values)
    if not form.validate_on_submit():
        return _render_book(form, details, values)

    isbn, replace_isbn = _isbn_change(details, form)
    try:
        update_book(
            get_session(),
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
    if set_copies(get_session(), current_user.id, book.id, copies):
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
    if author_param(values) not in deleted.authors_deleted:
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
        deleted = delete_book(get_session(), book_id)
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
