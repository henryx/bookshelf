from collections.abc import Callable
from uuid import uuid4

from flask import Flask
from flask.testing import FlaskClient

from bookshelf import create_app
from bookshelf.db import get_db
from bookshelf.models.users import Role, User, set_password, set_role

HTMX = {"HX-Request": "true"}


def _login(client: FlaskClient, email: str, password: str, **kwargs):
    return client.post("/login", data={"email": email, "password": password}, **kwargs)


def test_main_page_requires_login(db_client: FlaskClient):
    rv = db_client.get("/")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_htmx_request_without_login_redirects_via_header(db_client: FlaskClient):
    rv = db_client.get("/", headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/login"


def test_htmx_logout_with_expired_session_redirects_via_header(
    db_client: FlaskClient,
):
    rv = db_client.post("/logout", headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/login"


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


TOO_MANY_ATTEMPTS = "Too many failed login attempts"


def _fail_logins(client: FlaskClient, email: str, times: int) -> None:
    for _ in range(times):
        _login(client, email, "wrong")


def test_login_blocked_after_too_many_failures(
    db_client: FlaskClient, user: User, password: str
):
    _fail_logins(db_client, user.email, 5)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 429
    assert TOO_MANY_ATTEMPTS in rv.get_data(as_text=True)
    assert db_client.get("/").status_code == 302


def test_login_block_applies_to_unknown_email(db_client: FlaskClient):
    _fail_logins(db_client, "nobody@example.com", 5)

    rv = _login(db_client, "nobody@example.com", "wrong")

    assert rv.status_code == 429
    assert TOO_MANY_ATTEMPTS in rv.get_data(as_text=True)


def test_login_block_is_per_email(db_client: FlaskClient, user: User, password: str):
    _fail_logins(db_client, "nobody@example.com", 5)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def _add_old_failures(app: Flask, email: str, times: int, minutes_ago: int) -> None:
    with app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            for _ in range(times):
                cur.execute(
                    "INSERT INTO LOGIN_FAILURES (ID, EMAIL, ATTEMPTED_AT) "
                    "VALUES (?, ?, DATEADD(? MINUTE TO CURRENT_TIMESTAMP))",
                    (uuid4().bytes, email, -minutes_ago),
                )
        conn.commit()


def test_login_failures_expire(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    _add_old_failures(db_app, user.email, 5, minutes_ago=16)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def test_login_block_uses_configured_max_failures(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    db_app.config["LOGIN_MAX_FAILURES"] = 2
    _fail_logins(db_client, user.email, 2)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 429


def test_login_block_uses_configured_minutes(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    db_app.config["LOGIN_BLOCK_MINUTES"] = 10
    _add_old_failures(db_app, user.email, 5, minutes_ago=11)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def test_successful_login_clears_failures(
    db_client: FlaskClient, user: User, password: str
):
    _fail_logins(db_client, user.email, 4)
    _login(db_client, user.email, password)
    db_client.post("/logout")
    _fail_logins(db_client, user.email, 1)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


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


def test_password_reset_ends_existing_session(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    _login(db_client, user.email, password)
    with db_app.app_context():
        set_password(get_db(), user, "a brand new password")

    assert db_client.get("/").status_code == 302


def test_role_change_keeps_existing_session(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    _login(db_client, user.email, password)
    with db_app.app_context():
        set_role(get_db(), user, Role.ADMIN)

    assert db_client.get("/").status_code == 200


def test_login_requires_csrf_token(user: User, password: str):
    client = create_app({"TESTING": True}).test_client()

    rv = _login(client, user.email, password)

    assert rv.status_code == 400


def test_failed_login_logs_email_and_ip(db_client: FlaskClient, user: User, caplog):
    _login(db_client, user.email, "wrong")

    assert f"Failed login for {user.email} from 127.0.0.1" in caplog.messages


def test_blocked_login_logs_email_and_ip(db_client: FlaskClient, user: User, caplog):
    _fail_logins(db_client, user.email, 5)
    caplog.clear()

    _login(db_client, user.email, "wrong")

    assert any(
        m.startswith(f"Blocked login for {user.email} from 127.0.0.1")
        for m in caplog.messages
    )


def test_login_rejects_invalid_email_without_counting_it(
    db_client: FlaskClient, caplog
):
    rv = _login(db_client, "correct horse battery staple", "wrong")

    assert rv.status_code == 200
    assert "Invalid email address" in rv.get_data(as_text=True)
    assert not any("Failed login" in m for m in caplog.messages)


def test_login_ignores_spaces_around_email(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, f"  {user.email}  ", password)

    assert rv.status_code == 302
