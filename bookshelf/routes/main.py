from flask import Blueprint, render_template
from flask_login import login_required

__all__ = ["main_route"]

main_route = Blueprint("main", __name__)


@main_route.route("/", methods=("GET",))
@login_required
def index() -> str:
    return render_template("main.html")
