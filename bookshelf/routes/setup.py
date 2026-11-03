from firebird.driver import Connection
from flask import Blueprint, abort, current_app, render_template, request, url_for
from flask_login import login_user
from werkzeug.wrappers import Response as BaseResponse
from wtforms import PasswordField
from wtforms.validators import DataRequired, EqualTo

from bookshelf.db import get_db
from bookshelf.db.schema import init_db, table_exists
from bookshelf.models.users import Role, UserExistsError, admin_exists, create_user
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.auth import LoginForm

__all__ = ["setup_needed", "setup_route"]

setup_route = Blueprint("setup", __name__)

# Set once this process has seen an admin; setup never needs to run again.
_DONE_KEY = "bookshelf.setup_done"


class SetupForm(LoginForm):
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", "Passwords must match")],
    )


def setup_needed(conn: Connection) -> bool:
    """True until the schema exists and holds an admin."""
    return not table_exists(conn, "USERS") or not admin_exists(conn)


def _setup_done() -> bool:
    if current_app.extensions.get(_DONE_KEY):
        return True
    if setup_needed(get_db()):
        return False
    current_app.extensions[_DONE_KEY] = True
    return True


@setup_route.before_app_request
def require_setup() -> BaseResponse | None:
    """
    Send every request to the first-run wizard until an admin exists, and
    hide the wizard afterwards. Static files are served either way.
    """
    if request.endpoint == "static":
        return None
    done = _setup_done()
    if request.endpoint == "setup.wizard":
        if done:
            abort(404)
        return None
    if done:
        return None
    return htmx_redirect(url_for("setup.wizard"))


def _render_setup(form: SetupForm) -> str:
    template = "_setup_form.html" if is_htmx() else "setup.html"
    return render_template(template, form=form)


@setup_route.route("/setup", methods=("GET", "POST"))
def wizard() -> BaseResponse | str:
    """Create the schema and the first admin, then log the admin in."""
    form = SetupForm()
    if not form.validate_on_submit():
        return _render_setup(form)

    conn = get_db()
    init_db(conn)
    if admin_exists(conn):
        # Another visitor finished setup after this request passed the check.
        return htmx_redirect(url_for("auth.login"))
    try:
        admin = create_user(
            conn, form.email.data or "", form.password.data or "", role=Role.ADMIN
        )
    except UserExistsError as e:
        form.form_errors.append(str(e))
        return _render_setup(form)

    current_app.logger.info(
        "First admin %s created by setup wizard from %s",
        admin.email,
        request.remote_addr,
    )
    login_user(admin, remember=True)
    return htmx_redirect(url_for("main.index"))
