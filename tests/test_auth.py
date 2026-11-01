from collections.abc import Callable

from flask.testing import FlaskClient

from bookshelf import create_app
from bookshelf.models.users import User

HTMX = {"HX-Request": "true"}


def _login(client: FlaskClient, email: str, password: str, **kwargs):
    return client.post("/login", data={"email": email, "password": password}, **kwargs)


def test_main_page_requires_login(db_client: FlaskClient):
    rv = db_client.get("/")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_login_page_renders_form(db_client: FlaskClient):
    rv = db_client.get("/login")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" in html
    assert 'name="email"' in html
    assert 'name="password"' in html
    assert "hx-indicator" in html


def test_login_success_sets_persistent_cookie(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/"
    assert any("remember_token=" in c for c in rv.headers.getlist("Set-Cookie"))
    assert db_client.get("/").status_code == 200


def test_login_failure_shows_error(db_client: FlaskClient, user: User, password: str):
    rv = _login(db_client, user.email, "wrong")

    assert rv.status_code == 200
    assert "Invalid email or password" in rv.get_data(as_text=True)
    assert db_client.get("/").status_code == 302


def test_locked_user_cannot_login(
    db_client: FlaskClient, user: User, password: str, lock_user: Callable[[User], None]
):
    lock_user(user)

    rv = _login(db_client, user.email, password)

    assert "Invalid email or password" in rv.get_data(as_text=True)


def test_htmx_login_success_redirects_via_header(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, user.email, password, headers=HTMX)

    assert rv.headers["HX-Redirect"] == "/"


def test_htmx_login_failure_returns_partial(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, user.email, "wrong", headers=HTMX)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert "Invalid email or password" in html


def test_logout_returns_to_login(db_client: FlaskClient, user: User, password: str):
    _login(db_client, user.email, password)

    rv = db_client.post("/logout")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/login"
    assert db_client.get("/").status_code == 302


def test_locking_user_ends_existing_session(
    db_client: FlaskClient,
    user: User,
    password: str,
    lock_user: Callable[[User], None],
):
    _login(db_client, user.email, password)
    lock_user(user)

    assert db_client.get("/").status_code == 302


def test_login_requires_csrf_token(user: User, password: str):
    client = create_app({"TESTING": True}).test_client()

    rv = _login(client, user.email, password)

    assert rv.status_code == 400
