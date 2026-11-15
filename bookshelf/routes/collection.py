from datetime import UTC, datetime
from uuid import UUID

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    render_template,
    request,
)
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_db
from bookshelf.models.books import (
    SEARCH_MAX_LENGTH,
    Book,
    Sort,
    UnknownBookError,
    add_to_collection,
    find_book,
    list_collection,
    remove_from_collection,
    set_copies,
    set_read,
)
from bookshelf.models.collection_csv import (
    CsvFileError,
    ImportReport,
    export_collection,
    import_collection,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import CopiesForm, ImportForm, ReadForm
from bookshelf.routes.main import (
    author_filter,
    list_state,
    list_url,
    render_catalogue,
)

__all__ = ["collection_route"]

collection_route = Blueprint("collection", __name__, url_prefix="/collection")


def _render_collection(
    values: MultiDict[str, str],
    *,
    partial: bool,
    notice: str | None = None,
    error: str | None = None,
    error_details: list[str] | None = None,
) -> str:
    """
    The user's collection with the search, order, page and author (one
    author's books) in `values`: the list alone when `partial`, else the page. `notice` or `error` is shown
    above it, with `error_details` listed under the error.
    """
    search, sort, page = list_state(values)
    author = author_filter(values)
    books = list_collection(
        get_db(),
        current_user.id,
        search,
        sort,
        page,
        author_id=author.id if author else None,
    )
    return render_template(
        "_book_list.html" if partial else "collection.html",
        books=books,
        search=search,
        sort=sort,
        sorts=Sort,
        search_max_length=SEARCH_MAX_LENGTH,
        list_endpoint="collection.index",
        author=author,
        notice=notice,
        list_error=error,
        list_error_details=error_details,
    )


def _from_list() -> bool:
    """True for htmx requests from a book list's row, which swap the whole list."""
    return is_htmx() and request.headers.get("HX-Target") == "book-list"


def _book_or_404(book_id: UUID) -> Book:
    book = find_book(get_db(), current_user.id, book_id)
    if book is None:
        abort(404)
    return book


@collection_route.route("/", methods=("GET",))
@login_required
def index() -> str:
    """The current user's books. htmx searches and page changes get only the list."""
    partial = is_htmx() and not request.headers.get("HX-History-Restore-Request")
    return _render_collection(request.args, partial=partial)


@collection_route.post("/books/<uuid:book_id>/add")
@login_required
def add(book_id: UUID) -> BaseResponse | str:
    """
    Add a catalogue book to the current user's collection: from a row of
    the catalogue (the list is shown again, keeping its search and page), or
    from the add-book form when its ISBN is already in the catalogue.
    """
    book = _book_or_404(book_id)
    form = CopiesForm()
    if not form.validate_on_submit():
        abort(400, description=" ".join(form.copies.errors))
    try:
        added = add_to_collection(
            get_db(), current_user.id, book.id, form.copies.data or 1
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
    return htmx_redirect(list_url("main.index", request.form))


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


@collection_route.post("/books/<uuid:book_id>/copies")
@login_required
def copies(book_id: UUID) -> BaseResponse | str:
    """Change how many copies of a book in their collection the user owns."""
    book = _book_or_404(book_id)
    form = CopiesForm()
    if not form.validate_on_submit():
        error = " ".join(form.copies.errors)
        return _collection_response(f"Copies of “{book.title}”: {error}", error=True)
    count = form.copies.data or 1
    if not set_copies(get_db(), current_user.id, book.id, count):
        return _collection_response(
            f"“{book.title}” is not in your collection.", error=True
        )
    return _collection_response(f"Copies of “{book.title}” set to {count}.")


@collection_route.post("/books/<uuid:book_id>/read")
@login_required
def read(book_id: UUID) -> BaseResponse | str:
    """Mark a book in the user's collection as read, or not (the checkbox unticked)."""
    book = _book_or_404(book_id)
    form = ReadForm()
    if not form.validate_on_submit():
        abort(400, description="The request could not be read. Please try again.")
    is_read = bool(form.read.data)
    if not set_read(get_db(), current_user.id, book.id, read=is_read):
        return _collection_response(
            f"“{book.title}” is not in your collection.", error=True
        )
    state = "read" if is_read else "not read"
    return _collection_response(f"“{book.title}” marked as {state}.")


@collection_route.post("/books/<uuid:book_id>/remove")
@login_required
def remove(book_id: UUID) -> BaseResponse | str:
    """Remove a book from the user's collection; it stays in the catalogue."""
    book = _book_or_404(book_id)
    if not remove_from_collection(get_db(), current_user.id, book.id):
        return _collection_response(
            f"“{book.title}” is not in your collection.", error=True
        )
    current_app.logger.info(
        "Book %r removed from the collection of %s", book.title, current_user.email
    )
    return _collection_response(f"“{book.title}” removed from your collection.")


@collection_route.get("/export")
@login_required
def export() -> Response:
    """The current user's collection as a CSV download."""
    text = export_collection(get_db(), current_user.id)
    current_app.logger.info("Collection of %s exported", current_user.email)
    filename = f"bookshelf-collection-{datetime.now(UTC).date().isoformat()}.csv"
    return Response(
        text,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _import_notice(report: ImportReport) -> str:
    """What an import did: "2 books added to your collection (1 new to the catalogue)."."""
    if report.added:
        notice = f"{_plural(report.added, 'book')} added to your collection"
        if report.created:
            notice += f" ({report.created} new to the catalogue)"
    else:
        notice = "No books added"
    if report.skipped:
        notice += f"; {report.skipped} already in your collection, skipped"
    return f"{notice}."


@collection_route.post("/import")
@login_required
def import_csv() -> str:
    """
    Add the books of an uploaded CSV file to the current user's collection
    (see import_collection), then show the collection with what was done:
    the list alone for htmx, else the page (no redirect: the report is too
    long for a flashed message, and posting the file again only skips books).
    """
    partial = is_htmx()
    form = ImportForm()
    if not form.validate_on_submit():
        error = " ".join(form.file.errors) or "The file could not be read."
        return _render_collection(request.form, partial=partial, error=error)
    try:
        text = form.file.data.read().decode("utf-8-sig")
        report = import_collection(get_db(), current_user.id, text)
    except UnicodeDecodeError:
        error = "The file is not UTF-8 text."
        current_app.logger.info("Import by %s refused: not UTF-8", current_user.email)
        return _render_collection(request.form, partial=partial, error=error)
    except CsvFileError as e:
        current_app.logger.info("Import by %s refused: %s", current_user.email, e)
        return _render_collection(request.form, partial=partial, error=str(e))
    current_app.logger.info(
        "Import by %s: %d added (%d new to the catalogue), %d skipped, %d errors",
        current_user.email,
        report.added,
        report.created,
        report.skipped,
        len(report.errors),
    )
    error = None
    if report.errors:
        error = f"{_plural(len(report.errors), 'row')} not imported:"
    return _render_collection(
        request.form,
        partial=partial,
        notice=_import_notice(report),
        error=error,
        error_details=report.errors,
    )
