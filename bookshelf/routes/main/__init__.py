"""
The `main` blueprint: the catalogue (`catalogue.py`), adding a book
(`add_book.py`), editing and deleting one (`edit_book.py`), and the about page.
"""

from flask import Blueprint, render_template
from flask_login import login_required

__all__ = ["main_route"]

main_route = Blueprint("main", __name__)


@main_route.route("/about", methods=("GET",))
@login_required
def about() -> str:
    """
    The app's version, licence, source code and credits: the account menu
    opens them in a dialog, and this page without JavaScript.
    """
    return render_template("about.html")


# The views register their routes on main_route when imported.
from bookshelf.routes.main import add_book, catalogue, edit_book  # noqa: F401
