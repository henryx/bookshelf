import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.users import Role, User, create_user, find_by_email

HTMX = {"HX-Request": "true"}
ADMIN = {
    "email": " Admin@Example.com ",
    "password": "s3cret-pw",
    "confirm": "s3cret-pw",
}


@pytest.fixture
def setup_complete() -> None:
    """Override conftest's: these tests need the real first-run check."""


def _admin(app: Flask, email: str = "admin@example.com") -> User | None:
    with app.app_context():
        return find_by_email(get_db(), email)


def test_empty_database_redirects_to_setup(empty_db_app: Flask):
    rv = empty_db_app.test_client().get("/")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/setup"


def test_htmx_request_redirects_to_setup_via_header(empty_db_app: Flask):
    rv = empty_db_app.test_client().get("/login", headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/setup"


def test_static_files_are_served_during_setup(empty_db_app: Flask):
    rv = empty_db_app.test_client().get("/static/style.css")

    assert rv.status_code == 200


def test_setup_page_shows_form(empty_db_app: Flask):
    rv = empty_db_app.test_client().get("/setup")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert 'name="email"' in html
    assert 'name="confirm"' in html


def test_setup_creates_schema_and_admin_and_logs_in(empty_db_app: Flask):
    client = empty_db_app.test_client()

    rv = client.post("/setup", data=ADMIN)

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/"
    admin = _admin(empty_db_app)
    assert admin is not None
    assert admin.role is Role.ADMIN
    main = client.get("/")
    assert main.status_code == 200
    assert "admin@example.com" in main.get_data(as_text=True)


def test_htmx_setup_redirects_via_header(empty_db_app: Flask):
    rv = empty_db_app.test_client().post("/setup", data=ADMIN, headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/"


def test_setup_page_is_gone_once_done(empty_db_app: Flask):
    client = empty_db_app.test_client()
    client.post("/setup", data=ADMIN)

    rv = empty_db_app.test_client().get("/setup")

    assert rv.status_code == 404


def test_setup_rejects_mismatched_passwords(empty_db_app: Flask):
    client = empty_db_app.test_client()

    rv = client.post("/setup", data={**ADMIN, "confirm": "other"})

    assert rv.status_code == 200
    assert "Passwords must match" in rv.get_data(as_text=True)
    assert client.get("/").headers["Location"] == "/setup"


def test_setup_rejects_invalid_email(empty_db_app: Flask):
    client = empty_db_app.test_client()

    rv = client.post("/setup", data={**ADMIN, "email": "admin@bookshelf.local"})

    assert rv.status_code == 200
    assert 'class="error"' in rv.get_data(as_text=True)
    assert client.get("/").headers["Location"] == "/setup"


def test_htmx_invalid_setup_returns_partial(empty_db_app: Flask):
    rv = empty_db_app.test_client().post(
        "/setup", data={**ADMIN, "confirm": "other"}, headers=HTMX
    )
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert "Passwords must match" in html


def test_schema_without_admin_redirects_to_setup(db_client: FlaskClient, user: User):
    rv = db_client.get("/login")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/setup"


def test_setup_rejects_email_of_existing_user(db_client: FlaskClient, user: User):
    rv = db_client.post("/setup", data={**ADMIN, "email": user.email})

    assert rv.status_code == 200
    assert "already exists" in rv.get_data(as_text=True)
    assert db_client.get("/").headers["Location"] == "/setup"


def test_setup_refused_when_admin_created_meanwhile(
    db_client: FlaskClient, db_app: Flask, monkeypatch: pytest.MonkeyPatch
):
    """Another visitor finished setup after this request passed the check."""
    monkeypatch.setattr("bookshelf.routes.setup.setup_needed", lambda _conn: True)
    with db_app.app_context():
        create_user(get_db(), "first@example.com", "pw", role=Role.ADMIN)

    rv = db_client.post("/setup", data=ADMIN)

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/login"
    assert _admin(db_app) is None


def test_app_with_admin_skips_setup(db_client: FlaskClient, db_app: Flask):
    with db_app.app_context():
        create_user(get_db(), "admin@example.com", "pw", role=Role.ADMIN)

    assert db_client.get("/login").status_code == 200
    assert db_client.get("/setup").status_code == 404
