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

from bookshelf.db import get_session
from bookshelf.models.authors import (
    AuthorEntry,
    UnknownAuthorError,
    find_author,
    list_authors,
    rename_author,
)
from bookshelf.models.books import PAGE_SIZE, PAGE_SIZES, SEARCH_MAX_LENGTH
from bookshelf.routes import htmx_redirect, is_htmx, page_size
from bookshelf.routes.forms import RenameAuthorForm

__all__ = ["authors_route"]

authors_route = Blueprint("authors", __name__, url_prefix="/authors")


def _list_state(values: MultiDict[str, str]) -> tuple[str, int, int]:
    """The authors list's search, page and page size, from a query string or a form."""
    search = values.get("q", "").strip()
    return search, values.get("page", 1, type=int) or 1, page_size(values)


def _list_url(values: MultiDict[str, str]) -> str:
    search, page, size = _list_state(values)
    return url_for(
        "authors.index",
        q=search or None,
        page=page if page > 1 else None,
        size=None if size == PAGE_SIZE else size,
    )


@authors_route.route("/", methods=("GET",))
@login_required
def index() -> str:
    """Every author, with their books. htmx searches and page changes get only the list."""
    search, page, size = _list_state(request.args)
    partial = is_htmx() and not request.headers.get("HX-History-Restore-Request")
    return render_template(
        "_author_list.html" if partial else "authors.html",
        authors=list_authors(get_session(), search, page, page_size=size),
        search=search,
        size=size,
        page_sizes=PAGE_SIZES,
        default_size=PAGE_SIZE,
        search_max_length=SEARCH_MAX_LENGTH,
    )


def _render_rename(
    form: RenameAuthorForm, entry: AuthorEntry, values: MultiDict[str, str]
) -> str:
    """
    Full page without htmx; with htmx, the form alone (the rename dialog's
    content, and the response when it has errors). `values`: the list's
    search, page and page size, to go back to.
    """
    search, page, size = _list_state(values)
    return render_template(
        "_rename_author_form.html" if is_htmx() else "author.html",
        form=form,
        entry=entry,
        search=search,
        page=page,
        size=size,
        back_url=_list_url(values),
    )


@authors_route.route("/<uuid:author_id>", methods=("GET", "POST"))
@login_required
def rename(author_id: UUID) -> BaseResponse | str:
    """
    Rename an author for every book; any user may. A name another author
    already has merges the two. Saving goes back to the list as it was.
    """
    entry = find_author(get_session(), author_id)
    if entry is None:
        abort(404)
    form = RenameAuthorForm(
        data={"name": entry.author.name, "surname": entry.author.surname}
    )
    values = request.form if request.method == "POST" else request.args
    if not form.validate_on_submit():
        return _render_rename(form, entry, values)

    try:
        renamed = rename_author(get_session(), author_id, form.to_author())
    except UnknownAuthorError:
        # Merged into another author, or renamed into one, since it was looked up.
        current_app.logger.info(
            "Author %s (%r) gone before %s renamed it",
            author_id,
            str(entry.author),
            current_user.email,
        )
        abort(404)
    verb = "merged into" if renamed.merged else "renamed to"
    current_app.logger.info(
        "Author %r %s %r by %s",
        str(entry.author),
        verb,
        str(renamed.author),
        current_user.email,
    )
    flash(f"“{entry.author}” {verb} “{renamed.author}”.")
    return htmx_redirect(_list_url(values))
