import re
from collections.abc import Callable
from uuid import uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.users import (
    Role,
    User,
    authenticate,
    create_user,
    find_by_email,
)

HTMX = {"HX-Request": "true"}
ADMIN_EMAIL = "boss@example.com"
ADMIN_PASSWORD = "boss-password-1"
NEW_PASSWORD = "a-new-password"


@pytest.fixture
def admin(db_app: Flask) -> User:
    with db_app.app_context():
        return create_user(get_db(), ADMIN_EMAIL, ADMIN_PASSWORD, role=Role.ADMIN)


@pytest.fixture
def admin_client(db_client: FlaskClient, admin: User) -> FlaskClient:
    rv = db_client.post(
        "/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert rv.status_code == 302
    return db_client


def _user(app: Flask, email: str) -> User | None:
    with app.app_context():
        return find_by_email(get_db(), email)


def _existing(app: Flask, email: str) -> User:
    user = _user(app, email)
    assert user is not None
    return user


def _detail(user: User) -> str:
    return f"/admin/users/{user.id}"


def _save(client: FlaskClient, user: User, **fields: str):
    data = {"role": user.role.value, **fields}
    if user.is_locked and "locked" not in fields:
        data["locked"] = "y"
    return client.post(_detail(user), data=data)


# Access


def test_admin_area_requires_login(db_client: FlaskClient, admin: User):
    rv = db_client.get("/admin/users")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_admin_area_forbidden_to_regular_users(
    db_client: FlaskClient, admin: User, user: User, password: str
):
    db_client.post("/login", data={"email": user.email, "password": password})

    assert db_client.get("/admin/users").status_code == 403
    assert db_client.get(_detail(admin)).status_code == 403
    rv = db_client.post(_detail(user), data={"role": "ADMIN"})
    assert rv.status_code == 403
    assert _existing(db_client.application, user.email).role is Role.REGULAR


def test_demoted_admin_loses_access(
    admin_client: FlaskClient, db_app: Flask, admin: User
):
    with db_app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE USERS SET ROLE = 'REGULAR' WHERE ID = ?", (admin.id.bytes,)
            )
        conn.commit()

    assert admin_client.get("/admin/users").status_code == 403


def test_topbar_has_admin_button_for_admins_only(
    admin_client: FlaskClient, db_app: Flask, user: User, password: str
):
    assert 'href="/admin/users"' in admin_client.get("/").get_data(as_text=True)

    regular = db_app.test_client()
    regular.post("/login", data={"email": user.email, "password": password})
    assert 'href="/admin/users"' not in regular.get("/").get_data(as_text=True)


# List


def test_users_page_lists_users_linking_to_details(
    admin_client: FlaskClient, admin: User, user: User
):
    rv = admin_client.get("/admin/users")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert f'href="{_detail(admin)}"' in html
    assert f'href="{_detail(user)}"' in html
    assert user.email in html


def test_users_page_shows_names(admin_client: FlaskClient, db_app: Flask):
    with db_app.app_context():
        create_user(
            get_db(), "ada@example.com", NEW_PASSWORD, name="Ada", surname="Lovelace"
        )

    html = admin_client.get("/admin/users").get_data(as_text=True)

    assert "<th>Name</th>" in html
    assert "<td>Ada Lovelace</td>" in html


# Create

OPEN_DIALOG = re.compile(r'<dialog id="create-user-dialog"[^>]*\sopen[\s>]')


def test_create_user_form_is_in_closed_dialog_opened_by_button(
    admin_client: FlaskClient,
):
    html = admin_client.get("/admin/users").get_data(as_text=True)

    assert '<dialog id="create-user-dialog"' in html
    assert not OPEN_DIALOG.search(html)
    assert 'data-dialog-open="create-user-dialog"' in html


def test_invalid_create_without_htmx_shows_dialog_open(admin_client: FlaskClient):
    rv = admin_client.post(
        "/admin/users",
        data={"email": "new@example.com", "password": "short", "confirm": "short"},
    )
    html = rv.get_data(as_text=True)

    assert OPEN_DIALOG.search(html)
    assert "at least 12 characters" in html


def _create(client: FlaskClient, email: str, password: str = NEW_PASSWORD, **kw):
    data = {"email": email, "password": password, "confirm": password}
    return client.post("/admin/users", data=data, **kw)


def test_create_user(admin_client: FlaskClient, db_app: Flask):
    rv = _create(admin_client, " New.Reader@Example.com ")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/admin/users"
    created = _user(db_app, "new.reader@example.com")
    assert created is not None
    assert created.role is Role.REGULAR
    page = admin_client.get("/admin/users").get_data(as_text=True)
    assert "User new.reader@example.com created." in page


def test_create_user_form_asks_for_name_and_surname(admin_client: FlaskClient):
    html = admin_client.get("/admin/users").get_data(as_text=True)
    dialog = html[html.index('<dialog id="create-user-dialog"') :]

    assert 'name="name"' in dialog
    assert 'name="surname"' in dialog


@pytest.mark.parametrize(
    ("name", "surname", "saved"),
    [(" Ada ", "Lovelace", ("Ada", "Lovelace")), ("", "  ", (None, None))],
)
def test_create_user_with_name_and_surname(
    admin_client: FlaskClient, db_app: Flask, name, surname, saved
):
    data = {
        "email": "ada@example.com",
        "password": NEW_PASSWORD,
        "confirm": NEW_PASSWORD,
        "name": name,
        "surname": surname,
    }
    rv = admin_client.post("/admin/users", data=data)

    assert rv.status_code == 302
    created = _existing(db_app, "ada@example.com")
    assert (created.name, created.surname) == saved


def test_create_user_rejects_too_long_name(admin_client: FlaskClient, db_app: Flask):
    data = {
        "email": "ada@example.com",
        "password": NEW_PASSWORD,
        "confirm": NEW_PASSWORD,
        "name": "x" * 128,
    }
    html = admin_client.post("/admin/users", data=data).get_data(as_text=True)

    assert "Field cannot be longer than 127 characters." in html
    assert _user(db_app, "ada@example.com") is None


def test_htmx_create_user_redirects_via_header(admin_client: FlaskClient):
    rv = _create(admin_client, "new@example.com", headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/admin/users"


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [
        ("new@example.com", "short", "at least 12 characters"),
        ("new@bookshelf.local", NEW_PASSWORD, 'class="error"'),
    ],
)
def test_create_user_rejects_invalid_input(
    admin_client: FlaskClient, db_app: Flask, email: str, password: str, message: str
):
    rv = _create(admin_client, email, password, headers=HTMX)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert message in html
    assert _user(db_app, email) is None


def test_create_user_rejects_mismatched_passwords(
    admin_client: FlaskClient, db_app: Flask
):
    rv = admin_client.post(
        "/admin/users",
        data={"email": "new@example.com", "password": NEW_PASSWORD, "confirm": "x"},
    )

    assert "Passwords must match" in rv.get_data(as_text=True)
    assert _user(db_app, "new@example.com") is None


def test_create_user_rejects_existing_email(admin_client: FlaskClient, user: User):
    rv = _create(admin_client, user.email.upper())

    assert rv.status_code == 200
    assert "already exists" in rv.get_data(as_text=True)


# Details


def test_users_page_opens_details_in_dialog(admin_client: FlaskClient, user: User):
    html = admin_client.get("/admin/users").get_data(as_text=True)

    assert '<dialog id="user-dialog"' in html
    assert f'hx-get="{_detail(user)}"' in html
    assert 'hx-target="#user-dialog-body"' in html
    assert f'href="{_detail(user)}"' in html  # works without JavaScript too


def test_users_page_dialogs_are_in_the_page_body(admin_client: FlaskClient):
    """Dialogs must be elements in <main>, e.g. not text inside <title>."""
    html = admin_client.get("/admin/users").get_data(as_text=True)
    title = html[html.index("<title>") : html.index("</title>")]
    main = html[html.index("<main>") : html.index("</main>")]

    assert "<dialog" not in title
    assert '<dialog id="user-dialog"' in main
    assert 'id="user-dialog-body"' in main
    assert '<dialog id="create-user-dialog"' in main


def test_htmx_details_returns_dialog_content(admin_client: FlaskClient, user: User):
    rv = admin_client.get(_detail(user), headers=HTMX)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert 'class="topbar"' not in html
    assert 'id="user-form"' in html
    assert f'hx-post="{_detail(user)}/delete"' in html


def test_edit_dialog_ids_do_not_clash_with_create_dialog(
    admin_client: FlaskClient, user: User
):
    page = admin_client.get("/admin/users").get_data(as_text=True)
    details = admin_client.get(_detail(user), headers=HTMX).get_data(as_text=True)

    page_ids = set(re.findall(r'\sid="([^"]+)"', page))
    details_ids = set(re.findall(r'\sid="([^"]+)"', details))
    assert page_ids.isdisjoint(details_ids), page_ids & details_ids


def test_details_page_shows_editable_fields(admin_client: FlaskClient, user: User):
    rv = admin_client.get(_detail(user))
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert user.email in html
    assert 'name="email"' not in html
    assert 'name="role"' in html
    assert 'name="locked"' in html
    assert 'name="password"' in html
    assert 'name="csrf_token"' in html
    assert "At least 12 characters." in html


def test_details_page_shows_name_and_surname_filled_in(
    admin_client: FlaskClient, db_app: Flask
):
    with db_app.app_context():
        named = create_user(
            get_db(), "ada@example.com", NEW_PASSWORD, name="Ada", surname="Lovelace"
        )

    html = admin_client.get(_detail(named)).get_data(as_text=True)

    assert re.search(r'<input[^>]*name="name"[^>]*value="Ada"', html)
    assert re.search(r'<input[^>]*name="surname"[^>]*value="Lovelace"', html)


def test_save_sets_and_clears_name_and_surname(
    admin_client: FlaskClient, db_app: Flask, user: User
):
    _save(admin_client, user, name="Ada", surname=" Lovelace ")
    named = _existing(db_app, user.email)
    assert (named.name, named.surname) == ("Ada", "Lovelace")

    _save(admin_client, named, name="", surname="")
    cleared = _existing(db_app, user.email)
    assert (cleared.name, cleared.surname) == (None, None)


def test_own_details_page_cannot_change_role_or_lock(
    admin_client: FlaskClient, admin: User
):
    html = admin_client.get(_detail(admin)).get_data(as_text=True)

    assert 'type="checkbox"' not in html
    assert "<select" not in html
    assert f"{_detail(admin)}/delete" not in html


def test_save_applies_changes(
    admin_client: FlaskClient, db_app: Flask, user: User, password: str
):
    rv = _save(admin_client, user, role="ADMIN", locked="y")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/admin/users"
    saved = _existing(db_app, user.email)
    assert saved.role is Role.ADMIN
    assert saved.is_locked
    assert saved.password_hash == user.password_hash
    page = admin_client.get("/admin/users").get_data(as_text=True)
    assert f"User {user.email} saved." in page


def test_save_without_locked_unlocks(
    admin_client: FlaskClient,
    db_app: Flask,
    user: User,
    lock_user: Callable[[User], None],
):
    lock_user(user)

    admin_client.post(_detail(user), data={"role": "REGULAR"})

    assert not _existing(db_app, user.email).is_locked


def test_htmx_save_redirects_via_header(admin_client: FlaskClient, user: User):
    rv = admin_client.post(_detail(user), data={"role": "REGULAR"}, headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/admin/users"


def test_save_with_new_password(
    admin_client: FlaskClient, db_app: Flask, user: User, password: str
):
    _save(admin_client, user, password=NEW_PASSWORD, confirm=NEW_PASSWORD)

    with db_app.app_context():
        conn = get_db()
        assert authenticate(conn, user.email, NEW_PASSWORD) is not None
        assert authenticate(conn, user.email, password) is None


@pytest.mark.parametrize(
    ("new", "confirm", "message"),
    [
        ("short", "short", "at least 12 characters"),
        (NEW_PASSWORD, "different", "Passwords must match"),
    ],
)
def test_save_rejects_bad_password_and_changes_nothing(
    admin_client: FlaskClient,
    db_app: Flask,
    user: User,
    password: str,
    new: str,
    confirm: str,
    message: str,
):
    rv = admin_client.post(
        _detail(user),
        data={"role": "ADMIN", "password": new, "confirm": confirm},
        headers=HTMX,
    )
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert message in html
    unchanged = _existing(db_app, user.email)
    assert unchanged.role is Role.REGULAR
    with db_app.app_context():
        assert authenticate(get_db(), user.email, password) is not None


def test_save_rejects_invalid_role(
    admin_client: FlaskClient, db_app: Flask, user: User
):
    rv = admin_client.post(_detail(user), data={"role": "ROOT"})

    assert rv.status_code == 200
    assert _existing(db_app, user.email).role is Role.REGULAR


@pytest.mark.parametrize(
    "fields", [{"role": "REGULAR"}, {"role": "ADMIN", "locked": "y"}]
)
def test_admin_cannot_demote_or_lock_self(
    admin_client: FlaskClient, db_app: Flask, admin: User, fields: dict[str, str]
):
    rv = admin_client.post(_detail(admin), data=fields)

    assert rv.status_code == 200
    assert "You cannot demote or lock your own account." in rv.get_data(as_text=True)
    unchanged = _existing(db_app, ADMIN_EMAIL)
    assert unchanged.is_admin
    assert not unchanged.is_locked


def test_own_password_change_keeps_session(
    admin_client: FlaskClient, db_app: Flask, admin: User
):
    rv = _save(admin_client, admin, password=NEW_PASSWORD, confirm=NEW_PASSWORD)

    assert rv.status_code == 302
    assert admin_client.get("/admin/users").status_code == 200
    with db_app.app_context():
        assert authenticate(get_db(), ADMIN_EMAIL, NEW_PASSWORD) is not None


# Delete


@pytest.mark.parametrize("headers", [{}, HTMX])
def test_delete_user(
    admin_client: FlaskClient, db_app: Flask, user: User, headers: dict[str, str]
):
    rv = admin_client.post(f"{_detail(user)}/delete", headers=headers)

    if headers:
        assert rv.status_code == 204
        assert rv.headers["HX-Redirect"] == "/admin/users"
    else:
        assert rv.status_code == 302
        assert rv.headers["Location"] == "/admin/users"
    assert _user(db_app, user.email) is None
    page = admin_client.get("/admin/users").get_data(as_text=True)
    assert f"User {user.email} deleted." in page


def test_details_page_has_delete_with_confirmation(
    admin_client: FlaskClient, user: User
):
    html = admin_client.get(_detail(user)).get_data(as_text=True)

    assert f'hx-post="{_detail(user)}/delete"' in html
    assert "hx-confirm=" in html


def test_admin_cannot_delete_self(
    admin_client: FlaskClient, db_app: Flask, admin: User
):
    rv = admin_client.post(f"{_detail(admin)}/delete", headers=HTMX)

    assert rv.status_code == 400
    assert rv.headers["HX-Retarget"] == "#error-banner"
    assert "your own account" in rv.get_data(as_text=True)
    assert _user(db_app, ADMIN_EMAIL) is not None


@pytest.mark.parametrize(
    ("method", "suffix"), [("get", ""), ("post", ""), ("post", "/delete")]
)
def test_unknown_user_is_not_found(admin_client: FlaskClient, method: str, suffix: str):
    rv = getattr(admin_client, method)(f"/admin/users/{uuid4()}{suffix}")

    assert rv.status_code == 404
