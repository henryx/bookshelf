from flask import Blueprint, abort, render_template, request
from flask_login import login_required

from bookshelf.db import get_session
from bookshelf.models.books import (
    AuthorField,
    BookField,
    suggest_authors,
    suggest_books,
    suggest_categories,
    suggest_publishers,
)

__all__ = ["suggestions_route"]

suggestions_route = Blueprint("suggestions", __name__)


@suggestions_route.route("/authors/suggestions", methods=("GET",))
@login_required
def authors() -> str:
    """
    The options of an author row field's suggestion list: authors whose
    name (or surname, by `field`) starts with `q`.
    """
    try:
        field = AuthorField(request.args.get("field"))
    except ValueError:
        abort(400, description="Unknown author field.")
    found = suggest_authors(get_session(), request.args.get("q", ""), field)
    return render_template("_author_suggestions.html", authors=found)


@suggestions_route.route("/books/suggestions", methods=("GET",))
@login_required
def books() -> str:
    """
    The options of the add-book form's title or ISBN suggestion list (by
    `field`): catalogue books whose title or ISBN starts with `q`. Picking
    one loads it into the form (js/suggestions.js).
    """
    try:
        field = BookField(request.args.get("field"))
    except ValueError:
        abort(400, description="Unknown book field.")
    found = suggest_books(get_session(), request.args.get("q", ""), field)
    return render_template("_book_suggestions.html", books=found, field=field)


@suggestions_route.route("/publishers/suggestions", methods=("GET",))
@login_required
def publishers() -> str:
    """The options of the publisher's suggestion list: publishers starting with `q`."""
    found = suggest_publishers(get_session(), request.args.get("q", ""))
    return render_template("_publisher_suggestions.html", publishers=found)


@suggestions_route.route("/categories/suggestions", methods=("GET",))
@login_required
def categories() -> str:
    """The options of a category row's suggestion list: categories starting with `q`."""
    found = suggest_categories(get_session(), request.args.get("q", ""))
    return render_template("_category_suggestions.html", categories=found)
