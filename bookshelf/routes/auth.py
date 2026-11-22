from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from authlib.integrations.flask_client.apps import FlaskOAuth2App
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
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
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_db
from bookshelf.models.users import (
    User,
    authenticate,
    clear_login_failures,
    find_by_session_id,
    google_login,
    start_login_attempt,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import LoginForm
from bookshelf.routes.setup import setup_done

__all__ = ["auth_route", "google_client", "init_google", "login_manager"]

auth_route = Blueprint("auth", __name__)

login_manager = LoginManager()
login_manager.login_view = "auth.login"

INVALID_CREDENTIALS = "Invalid email or password"
TOO_MANY_ATTEMPTS = "Too many failed login attempts. Please try again later."
GOOGLE_LOGIN_REFUSED = (
    "This Google account cannot log in to Bookshelf. Ask an admin to add you."
)
GOOGLE_LOGIN_FAILED = "Logging in with Google failed. Please try again."

_GOOGLE_KEY = "bookshelf.google"


def init_google(app: Flask) -> None:
    """
    Set up logging in with Google (OpenID Connect) when GOOGLE_CLIENT_ID and
    GOOGLE_CLIENT_SECRET are set. Google's endpoints are given here rather
    than fetched from its discovery document, so no request is made until
    someone logs in. The scope asks only for the account's ID and email.
    """
    if not app.config.get("GOOGLE_CLIENT_ID"):
        return
    app.extensions[_GOOGLE_KEY] = OAuth(app).register(
        "google",
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        access_token_url="https://oauth2.googleapis.com/token",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        issuer="https://accounts.google.com",
        client_kwargs={"scope": "openid email"},
    )


def google_client() -> FlaskOAuth2App:
    """The app's Google client; 404 when logging in with Google is not set up."""
    client = current_app.extensions.get(_GOOGLE_KEY)
    if client is None:
        abort(404)
    return client


@login_manager.user_loader
def load_user(session_id: str) -> User | None:
    """
    Reload the user on every request; locked users, and sessions issued
    before a password change, are logged out. Until setup is done nobody is
    logged in: a cookie from an earlier database must not query a USERS table
    that may not exist yet.
    """
    if not setup_done():
        return None
    user = find_by_session_id(get_db(), session_id)
    if user is None or not user.is_active:
        return None
    return user


@login_manager.unauthorized_handler
def unauthorized() -> BaseResponse:
    """
    Send anonymous users to the login page. htmx requests get HX-Redirect,
    otherwise htmx would follow a 302 and swap the full login page into
    the request's target.
    """
    return htmx_redirect(url_for("auth.login"))


def _render_login(form: LoginForm) -> str:
    template = "_login_form.html" if is_htmx() else "login.html"
    return render_template(template, form=form)


@auth_route.route("/login", methods=("GET", "POST"))
def login() -> BaseResponse | str | tuple[str, int]:
    if current_user.is_authenticated:
        return htmx_redirect(url_for("collection.index"))

    form = LoginForm()
    if not form.validate_on_submit():
        return _render_login(form)

    conn = get_db()
    email = form.email.data or ""
    ip = request.remote_addr or ""
    cfg = current_app.config
    # Counted as a failure until the password checks out; blocked attempts
    # are not checked or counted, so retrying does not extend the block.
    if not start_login_attempt(
        conn,
        email,
        ip,
        max_failures=cfg["LOGIN_MAX_FAILURES"],
        max_ip_failures=cfg["LOGIN_MAX_IP_FAILURES"],
        window_minutes=cfg["LOGIN_BLOCK_MINUTES"],
    ):
        current_app.logger.warning(
            "Blocked login for %s from %s: too many failed attempts", email, ip
        )
        form.form_errors.append(TOO_MANY_ATTEMPTS)
        return _render_login(form), 429

    user = authenticate(conn, email, form.password.data or "")
    if user is None:
        current_app.logger.info("Failed login for %s from %s", email, ip)
        form.form_errors.append(INVALID_CREDENTIALS)
        return _render_login(form)

    clear_login_failures(conn, email, ip)
    login_user(user, remember=True)
    return htmx_redirect(url_for("collection.index"))


@auth_route.get("/login/google")
def google_start() -> BaseResponse:
    """Send the browser to Google, which sends it back to google_callback."""
    if current_user.is_authenticated:
        return redirect(url_for("collection.index"))
    return google_client().authorize_redirect(
        url_for("auth.google_callback", _external=True)
    )


@auth_route.get("/login/google/callback")
def google_callback() -> BaseResponse:
    """
    Google's answer: Authlib checks it belongs to the login this browser
    started (state, nonce) and the ID token's signature, then the Google
    account logs in as its Google user, if there is one and it is active.
    """
    client = google_client()
    try:
        token = client.authorize_access_token()
    except OAuthError as e:
        current_app.logger.warning(
            "Google login failed from %s: %s", request.remote_addr, e
        )
        flash(GOOGLE_LOGIN_FAILED)
        return redirect(url_for("auth.login"))
    claims = token.get("userinfo") or {}
    email = str(claims.get("email") or "")
    user = None
    if claims.get("email_verified") and claims.get("sub"):
        user = google_login(get_db(), str(claims["sub"]), email)
    if user is None or not user.is_active:
        current_app.logger.info(
            "Google login refused for %s from %s", email, request.remote_addr
        )
        flash(GOOGLE_LOGIN_REFUSED)
        return redirect(url_for("auth.login"))
    current_app.logger.info(
        "Google login for %s from %s", user.email, request.remote_addr
    )
    login_user(user, remember=True)
    return redirect(url_for("collection.index"))


@auth_route.route("/logout", methods=("POST",))
@login_required
def logout() -> BaseResponse:
    logout_user()
    return htmx_redirect(url_for("auth.login"))
