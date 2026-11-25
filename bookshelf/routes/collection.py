from uuid import UUID

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    render_template,
    request,
)
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_session
from bookshelf.models.books import (
    MAX_SCORE,
    PAGE_SIZE,
    PAGE_SIZES,
    SEARCH_MAX_LENGTH,
    Book,
    Sort,
    UnknownBookError,
    add_to_collection,
    find_book,
    list_collection,
    remove_from_collection,
    set_read,
    set_score,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.book_lists import (
    author_filter,
    book_list_endpoint,
    list_state,
    list_url,
)
from bookshelf.routes.forms import BookForm, CopiesForm, ReadForm, ScoreForm
from bookshelf.routes.main.catalogue import render_catalogue

__all__ = ["collection_route"]

collection_route = Blueprint("collection", __name__)


def _render_collection(
    values: MultiDict[str, str],
    *,
    partial: bool,
    notice: str | None = None,
    error: str | None = None,
) -> str:
    """
    The user's collection with the search, order, page, page size and author (one
    author's books) in `values`: the list alone when `partial`, else the page. `notice` or `error` is shown
    above it.
    """
    search, sort, page, size = list_state(values)
    author = author_filter(values)
    books = list_collection(
        get_session(),
        current_user.id,
        search,
        sort,
        page,
        author_id=author.id if author else None,
        page_size=size,
    )
    return render_template(
        "_book_list.html" if partial else "collection.html",
        books=books,
        search=search,
        sort=sort,
        sorts=Sort,
        size=size,
        page_sizes=PAGE_SIZES,
        default_size=PAGE_SIZE,
        search_max_length=SEARCH_MAX_LENGTH,
        list_endpoint="collection.index",
        author=author,
        notice=notice,
        list_error=error,
        form=BookForm(formdata=None),
        add_list="collection",
    )


def _from_list() -> bool:
    """True for htmx requests from a book list's row, which swap the whole list."""
    return is_htmx() and request.headers.get("HX-Target") == "book-list"


def _book_or_404(book_id: UUID) -> Book:
    book = find_book(get_session(), current_user.id, book_id)
    if book is None:
        abort(404)
    return book


@collection_route.route("/", methods=("GET",))
@login_required
def index() -> str:
    """The current user's books. htmx searches and page changes get only the list."""
    partial = is_htmx() and not request.headers.get("HX-History-Restore-Request")
    return _render_collection(request.args, partial=partial)


@collection_route.post("/collection/books/<uuid:book_id>/add")
@login_required
def add(book_id: UUID) -> BaseResponse | str:
    """
    Add a catalogue book to the current user's collection: from a row of
    the catalogue (the list is shown again, keeping its search and page), or
    from the add-book form when its ISBN is already in the catalogue (back to
    the list the form was opened from).
    """
    book = _book_or_404(book_id)
    form = CopiesForm()
    if not form.validate_on_submit():
        abort(400, description=" ".join(form.copies.errors))
    try:
        added = add_to_collection(
            get_session(), current_user.id, book.id, form.copies.data or 1
        )
    except UnknownBookError:
        # Deleted since it was looked up.
        current_app.logger.info("Book %r left the catalogue before adding", book.title)
        abort(404)
    if added:
        current_app.logger.info(
            "Book %r added to the collection of %s", book.title, current_user.email
        )
        notice = f"“{book.title}” added to your collection."
    else:
        notice = f"“{book.title}” is already in your collection."
    if _from_list():
        return render_catalogue(request.form, partial=True, notice=notice)
    flash(notice)
    return htmx_redirect(list_url(book_list_endpoint(request.form), request.form))


def _collection_response(message: str, *, error: bool = False) -> BaseResponse | str:
    """
    The collection list again with `message` for htmx; otherwise a redirect
    to the page, with `message` flashed.
    """
    if _from_list():
        if error:
            return _render_collection(request.form, partial=True, error=message)
        return _render_collection(request.form, partial=True, notice=message)
    flash(message)
    return htmx_redirect(list_url("collection.index", request.form))


@collection_route.post("/collection/books/<uuid:book_id>/read")
@login_required
def read(book_id: UUID) -> BaseResponse | str:
    """Mark a book in the user's collection as read, or not (the checkbox unticked)."""
    book = _book_or_404(book_id)
    # The app-wide CSRFProtect has already checked the token; the checkbox
    # itself cannot be invalid.
    is_read = bool(ReadForm().read.data)
    if not set_read(get_session(), current_user.id, book.id, read=is_read):
        return _collection_response(
            f"“{book.title}” is not in your collection.", error=True
        )
    state = "read" if is_read else "not read"
    return _collection_response(f"“{book.title}” marked as {state}.")


@collection_route.post("/collection/books/<uuid:book_id>/score")
@login_required
def score(book_id: UUID) -> BaseResponse | str:
    """
    Score a book in the user's collection with the star clicked; clicking
    the star of its score again clears it.
    """
    book = _book_or_404(book_id)
    form = ScoreForm()
    if not form.validate_on_submit():
        abort(400, description=" ".join(form.score.errors))
    clicked = form.score.data
    new_score = None if clicked == book.score else clicked
    if not set_score(get_session(), current_user.id, book.id, new_score):
        return _collection_response(
            f"“{book.title}” is not in your collection.", error=True
        )
    if new_score is None:
        return _collection_response(f"Score of “{book.title}” cleared.")
    return _collection_response(f"“{book.title}” scored {new_score} of {MAX_SCORE}.")


@collection_route.post("/collection/books/<uuid:book_id>/remove")
@login_required
def remove(book_id: UUID) -> BaseResponse | str:
    """Remove a book from the user's collection; it stays in the catalogue."""
    book = _book_or_404(book_id)
    if not remove_from_collection(get_session(), current_user.id, book.id):
        return _collection_response(
            f"“{book.title}” is not in your collection.", error=True
        )
    current_app.logger.info(
        "Book %r removed from the collection of %s", book.title, current_user.email
    )
    return _collection_response(f"“{book.title}” removed from your collection.")
