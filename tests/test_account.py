"""A user's own account page, and the avatar menu that links to it."""

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.users import (
    AuthMethod,
    Role,
    User,
    authenticate,
    create_user,
    find_by_email,
)

HTMX = {"HX-Request": "true"}
NEW_PASSWORD = "a-new-password"


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


def _saved(app: Flask, email: str) -> User:
    with app.app_context():
        found = find_by_email(get_db(), email)
    assert found is not None
    return found


def _save(client: FlaskClient, headers: dict[str, str] | None = None, **fields: str):
    return client.post("/account", data=fields, headers=headers or {})


# The page


def test_account_page_requires_login(db_client: FlaskClient):
    rv = db_client.get("/account")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_account_page_shows_email_and_editable_fields(client: FlaskClient, user: User):
    rv = client.get("/account")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" in html
    assert user.email in html
    assert 'name="email"' not in html
    for field in ("name", "surname", "current_password", "password", "confirm"):
        assert f'name="{field}"' in html
    assert 'name="role"' not in html
    assert "At least 12 characters." in html


def test_htmx_account_page_returns_only_the_form(client: FlaskClient):
    html = client.get("/account", headers=HTMX).get_data(as_text=True)

    assert "<html" not in html
    assert 'id="account-form"' in html


# Name and surname


def test_save_name_and_surname(client: FlaskClient, db_app: Flask, user: User):
    rv = _save(client, name=" Ada ", surname="Lovelace")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/account"
    saved = _saved(db_app, user.email)
    assert (saved.name, saved.surname) == ("Ada", "Lovelace")
    assert "Your account was saved." in client.get("/account").get_data(as_text=True)


def test_blank_name_and_surname_are_cleared(
    client: FlaskClient, db_app: Flask, user: User
):
    _save(client, name="Ada", surname="Lovelace")
    _save(client, name="", surname=" ")

    saved = _saved(db_app, user.email)
    assert (saved.name, saved.surname) == (None, None)


def test_htmx_save_redirects_via_header(client: FlaskClient):
    rv = _save(client, HTMX, name="Ada")

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/account"


def test_save_cannot_change_role(client: FlaskClient, db_app: Flask, user: User):
    _save(client, name="Ada", role="ADMIN")

    assert _saved(db_app, user.email).role is Role.REGULAR


def test_save_rejects_too_long_name(client: FlaskClient, db_app: Flask, user: User):
    html = _save(client, name="x" * 128).get_data(as_text=True)

    assert "Field cannot be longer than 127 characters." in html
    assert _saved(db_app, user.email).name is None


# Password


def test_change_password_with_current_one(
    client: FlaskClient, db_app: Flask, user: User, password: str
):
    rv = _save(
        client,
        current_password=password,
        password=NEW_PASSWORD,
        confirm=NEW_PASSWORD,
    )

    assert rv.status_code == 302
    with db_app.app_context():
        conn = get_db()
        assert authenticate(conn, user.email, NEW_PASSWORD) is not None
        assert authenticate(conn, user.email, password) is None
    # The new password ends other sessions, but renews this one.
    assert client.get("/account").status_code == 200


@pytest.mark.parametrize(
    ("current", "new", "confirm", "message"),
    [
        (
            "wrong password!",
            NEW_PASSWORD,
            NEW_PASSWORD,
            "Current password is incorrect.",
        ),
        ("", NEW_PASSWORD, NEW_PASSWORD, "Enter your current password to change it."),
        (None, "short", "short", "at least 12 characters"),
        (None, NEW_PASSWORD, "different", "Passwords must match"),
    ],
)
def test_bad_password_change_is_refused_and_changes_nothing(
    client: FlaskClient,
    db_app: Flask,
    user: User,
    password: str,
    current: str | None,
    new: str,
    confirm: str,
    message: str,
):
    rv = _save(
        client,
        name="Ada",
        current_password=password if current is None else current,
        password=new,
        confirm=confirm,
    )

    assert rv.status_code == 200
    assert message in rv.get_data(as_text=True)
    saved = _saved(db_app, user.email)
    assert saved.name is None
    assert saved.password_hash == user.password_hash


# Avatar menu


def _avatar(html: str) -> str:
    start = html.index('<details class="avatar"')
    return html[start : html.index("</details>", start)]


def test_avatar_shows_initials_and_full_name(db_client: FlaskClient, db_app: Flask):
    with db_app.app_context():
        create_user(
            get_db(), "ada@example.com", NEW_PASSWORD, name="Ada", surname="Lovelace"
        )
    db_client.post(
        "/login", data={"email": "ada@example.com", "password": NEW_PASSWORD}
    )

    avatar = _avatar(db_client.get("/").get_data(as_text=True))

    assert re.search(r'class="avatar-initials"[^>]*>AL<', avatar)
    assert "Ada Lovelace" in avatar
    assert "ada@example.com" in avatar


def test_avatar_without_name_shows_email(client: FlaskClient, user: User):
    avatar = _avatar(client.get("/").get_data(as_text=True))

    assert re.search(r'class="avatar-initials"[^>]*>R<', avatar)
    assert user.email in avatar


def test_avatar_menu_links_to_account_and_logs_out(client: FlaskClient):
    avatar = _avatar(client.get("/").get_data(as_text=True))

    assert 'href="/account"' in avatar
    assert 'action="/logout"' in avatar
    assert 'href="/admin/users"' not in avatar


# A user logging in with Google


@pytest.fixture
def google_client(db_app: Flask, db_client: FlaskClient) -> FlaskClient:
    """Logged in as a Google user: their session, as a Google login leaves it."""
    with db_app.app_context():
        google = create_user(
            get_db(), "gina@example.com", None, auth_method=AuthMethod.GOOGLE
        )
    with db_client.session_transaction() as session:
        session["_user_id"] = google.get_id()
        session["_fresh"] = True
    return db_client


def test_google_user_has_no_password_to_change(google_client: FlaskClient):
    html = google_client.get("/account").get_data(as_text=True)

    assert "You log in with Google" in html
    assert "Change password" not in html
    assert 'name="current_password"' not in html


def test_google_user_saves_their_name(google_client: FlaskClient, db_app: Flask):
    rv = google_client.post("/account", data={"name": "Gina", "surname": "Rossi"})

    assert rv.status_code == 302
    with db_app.app_context():
        saved = find_by_email(get_db(), "gina@example.com")
    assert saved is not None
    assert (saved.name, saved.surname) == ("Gina", "Rossi")
    assert google_client.get("/account").status_code == 200  # still logged in
