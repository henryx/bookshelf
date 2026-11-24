from flask import Blueprint, abort, current_app, render_template, request, url_for
from flask_login import login_user
from sqlalchemy.orm import Session
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_engine, get_session
from bookshelf.db.catalog import table_exists
from bookshelf.db.schema import init_db, schema_behind
from bookshelf.models.users import (
    Role,
    UserExistsError,
    admin_exists,
    create_user,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import SetupForm
from bookshelf.setup_token import setup_token

__all__ = ["setup_done", "setup_needed", "setup_route"]

setup_route = Blueprint("setup", __name__)

# Set once this process has seen an admin; setup never needs to run again.
_DONE_KEY = "bookshelf.setup_done"
# Set once this process has seen the schema up to date.
_SCHEMA_CURRENT_KEY = "bookshelf.schema_current"
# Set once this process has logged the setup token.
_TOKEN_LOGGED_KEY = "bookshelf.setup_token_logged"


def _log_token_once() -> None:
    """Log the token the first time this process serves the wizard."""
    if current_app.extensions.get(_TOKEN_LOGGED_KEY):
        return
    current_app.logger.warning(
        "Setup is not done: the setup wizard (/setup) asks for the token %s",
        setup_token(current_app.config["SECRET_KEY"]),
    )
    current_app.extensions[_TOKEN_LOGGED_KEY] = True


def setup_needed(session: Session) -> bool:
    """True until the schema exists and holds an admin."""
    return not table_exists(session.connection(), "USERS") or not admin_exists(session)


def setup_done() -> bool:
    """True once an admin exists; cached per process after that."""
    if current_app.extensions.get(_DONE_KEY):
        return True
    if setup_needed(get_session()):
        return False
    current_app.extensions[_DONE_KEY] = True
    return True


def _serves_without_database() -> bool:
    """Static files, the service worker and its offline page."""
    return request.endpoint == "static" or request.blueprint == "pwa"


@setup_route.before_app_request
def require_current_schema() -> None:
    """
    Answer 503 while the database schema is older than this release's, until
    init-db upgrades it. Checked until this process has seen it up to date.
    """
    if current_app.extensions.get(_SCHEMA_CURRENT_KEY) or _serves_without_database():
        return
    if schema_behind(get_session().connection()):
        current_app.logger.error("Database schema is behind the code: run init-db")
        abort(503, description="The database needs upgrading: run init-db.")
    current_app.extensions[_SCHEMA_CURRENT_KEY] = True


@setup_route.before_app_request
def require_setup() -> BaseResponse | None:
    """
    Send every request to the first-run wizard until an admin exists, and
    hide the wizard afterwards. Static files, the service worker and its
    offline page are served either way.
    """
    if _serves_without_database():
        return None
    done = setup_done()
    if request.endpoint == "setup.wizard":
        if done:
            abort(404)
        return None
    if done:
        return None
    return htmx_redirect(url_for("setup.wizard"))


def _render_setup(form: SetupForm) -> str:
    _log_token_once()
    template = "_setup_form.html" if is_htmx() else "setup.html"
    return render_template(template, form=form)


@setup_route.route("/setup", methods=("GET", "POST"))
def wizard() -> BaseResponse | str:
    """Create the schema and the first admin, then log the admin in."""
    form = SetupForm()
    if not form.validate_on_submit():
        return _render_setup(form)

    init_db(get_engine(), search_language=current_app.config["SEARCH_LANGUAGE"])
    session = get_session()
    # A new transaction: this request's began before init_db created the schema.
    session.rollback()
    if admin_exists(session):
        # Another visitor finished setup after this request passed the check.
        return htmx_redirect(url_for("auth.login"))
    try:
        admin = create_user(
            session,
            form.email.data or "",
            form.password.data or "",
            role=Role.ADMIN,
            min_password_length=current_app.config["MIN_PASSWORD_LENGTH"],
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
    return htmx_redirect(url_for("collection.index"))
