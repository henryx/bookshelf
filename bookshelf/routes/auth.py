from flask import (
    Blueprint,
    Response,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf import FlaskForm
from werkzeug.wrappers import Response as BaseResponse
from wtforms import EmailField, PasswordField
from wtforms.validators import DataRequired, Length

from bookshelf.db import get_db
from bookshelf.models.users import (
    User,
    authenticate,
    clear_login_failures,
    find_by_session_id,
    is_login_blocked,
    record_login_failure,
)
from bookshelf.routes import is_htmx

__all__ = ["auth_route", "login_manager"]

auth_route = Blueprint("auth", __name__)

login_manager = LoginManager()
login_manager.login_view = "auth.login"

INVALID_CREDENTIALS = "Invalid email or password"
TOO_MANY_ATTEMPTS = "Too many failed login attempts. Please try again later."


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired()])


@login_manager.user_loader
def load_user(session_id: str) -> User | None:
    """
    Reload the user on every request; locked users, and sessions issued
    before a password change, are logged out.
    """
    user = find_by_session_id(get_db(), session_id)
    if user is None or not user.is_active:
        return None
    return user


def _redirect(url: str) -> BaseResponse:
    """
    Redirect that works for both HTMX and plain requests.
    """
    if not is_htmx():
        return redirect(url)
    resp = Response(status=204)
    resp.headers["HX-Redirect"] = url
    return resp


@login_manager.unauthorized_handler
def unauthorized() -> BaseResponse:
    """
    Send anonymous users to the login page. htmx requests get HX-Redirect,
    otherwise htmx would follow a 302 and swap the full login page into
    the request's target.
    """
    return _redirect(url_for("auth.login"))


def _render_login(form: LoginForm) -> str:
    template = "_login_form.html" if is_htmx() else "login.html"
    return render_template(template, form=form)


@auth_route.route("/login", methods=("GET", "POST"))
def login() -> BaseResponse | str | tuple[str, int]:
    if current_user.is_authenticated:
        return _redirect(url_for("main.index"))

    form = LoginForm()
    if not form.validate_on_submit():
        return _render_login(form)

    conn = get_db()
    email = form.email.data or ""
    max_failures = current_app.config["LOGIN_MAX_FAILURES"]
    window_minutes = current_app.config["LOGIN_BLOCK_MINUTES"]
    # Blocked attempts are not checked or counted, so retrying does not extend the block.
    if is_login_blocked(
        conn, email, max_failures=max_failures, window_minutes=window_minutes
    ):
        current_app.logger.warning(
            "Blocked login for %s from %s: too many failed attempts",
            email,
            request.remote_addr,
        )
        form.form_errors.append(TOO_MANY_ATTEMPTS)
        return _render_login(form), 429

    user = authenticate(conn, email, form.password.data or "")
    if user is None:
        current_app.logger.info(
            "Failed login for %s from %s", email, request.remote_addr
        )
        record_login_failure(conn, email, window_minutes=window_minutes)
        form.form_errors.append(INVALID_CREDENTIALS)
        return _render_login(form)

    clear_login_failures(conn, email)
    login_user(user, remember=True)
    return _redirect(url_for("main.index"))


@auth_route.route("/logout", methods=("POST",))
@login_required
def logout() -> BaseResponse:
    logout_user()
    return _redirect(url_for("auth.login"))
