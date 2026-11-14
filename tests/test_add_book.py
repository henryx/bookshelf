"""Adding a book from the main page."""

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import (
    InvalidIsbnError,
    NewBook,
    add_book,
    list_books,
    normalise_isbn,
)
from bookshelf.models.users import User

HTMX = {"HX-Request": "true"}
OPEN_DIALOG = re.compile(r'<dialog id="add-book-dialog"[^>]*\sopen[\s>]')


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


def _post(client: FlaskClient, **fields: str):
    data = {"title": "Dune", "copies": "1", **fields}
    return client.post("/books/new", data=data, headers=HTMX)


def _scalar(app: Flask, sql: str, params: tuple[object, ...] = ()) -> object:
    with app.app_context(), get_db().cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def _rows(app: Flask, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
    with app.app_context(), get_db().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# The button and the dialog


def test_main_page_has_floating_add_button_opening_closed_dialog(client):
    html = client.get("/").get_data(as_text=True)

    assert re.search(r'<a [^>]*class="fab"', html)
    assert 'data-dialog-open="add-book-dialog"' in html
    assert 'href="/books/new"' in html
    assert '<dialog id="add-book-dialog"' in html
    assert not OPEN_DIALOG.search(html)
    assert 'name="title"' in html


def test_add_page_works_without_javascript(client):
    rv = client.get("/books/new")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" in html
    assert 'name="title"' in html


def test_add_page_requires_login(db_client):
    rv = db_client.get("/books/new")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


# Adding


def test_add_book_saves_it_in_the_catalogue_and_collection(db_app, client, user):
    rv = _post(
        client,
        title="  Good Omens ",
        authors="Terry Pratchett\n\n  Neil Gaiman  \n",
        isbn="0-552-13703-0",
        publisher="Gollancz",
        publication_year="1990",
        language="English",
        tags="fantasy, Humour,  , FANTASY",
        notes="Signed copy",
        copies="2",
    )

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/"
    row = _rows(
        db_app,
        "SELECT ID, TITLE, ISBN, PUBLISHER, PUBLICATION_YEAR, LANGUAGE, NOTES FROM BOOKS",
    )
    assert len(row) == 1
    book_id, *details = row[0]
    assert details == [
        "Good Omens",
        "9780552137034",
        "Gollancz",
        1990,
        "English",
        "Signed copy",
    ]
    authors = _rows(
        db_app,
        "SELECT a.NAME FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID "
        "WHERE ba.BOOK_ID = ? ORDER BY ba.SORT_ORDER",
        (book_id,),
    )
    assert [name for (name,) in authors] == ["Terry Pratchett", "Neil Gaiman"]
    tags = _rows(
        db_app, "SELECT TAG FROM BOOK_TAGS WHERE BOOK_ID = ? ORDER BY TAG", (book_id,)
    )
    assert [tag for (tag,) in tags] == ["fantasy", "Humour"]
    copies = _scalar(
        db_app,
        "SELECT COPIES FROM COLLECTION_BOOKS WHERE USER_ID = ? AND BOOK_ID = ?",
        (user.id.bytes, book_id),
    )
    assert copies == 2


def test_add_book_shows_confirmation_on_the_list(client):
    _post(client, title="Dune")

    html = client.get("/").get_data(as_text=True)

    assert "Book “Dune” added." in html
    assert '<span class="book-title">Dune</span>' in html


def test_add_book_with_title_only_leaves_the_rest_empty(db_app, client):
    _post(client, title="Untitled notes")

    row = _rows(
        db_app,
        "SELECT ISBN, PUBLISHER, PUBLICATION_YEAR, LANGUAGE, NOTES FROM BOOKS",
    )
    assert row == [(None, None, None, None, None)]


def test_add_book_reuses_existing_author_ignoring_case(db_app, client):
    _post(client, title="Mort", authors="Terry Pratchett")
    _post(client, title="Guards! Guards!", authors="TERRY PRATCHETT")

    names = _rows(db_app, "SELECT NAME FROM AUTHORS")
    assert names == [("Terry Pratchett",)]


def test_add_book_drops_repeated_author(db_app, client):
    _post(client, authors="Ann Leckie\nann leckie")

    assert _scalar(db_app, "SELECT COUNT(*) FROM BOOK_AUTHORS") == 1


def test_add_book_without_htmx_redirects(client):
    data = {"title": "Dune", "copies": "1"}
    rv = client.post("/books/new", data=data)

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/"


# Errors


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"title": "   "}, "This field is required."),
        ({"title": "x" * 256}, "Field cannot be longer than 255 characters."),
        ({"isbn": "978-0-441-17271-8"}, "Not a valid ISBN."),
        ({"isbn": "12345"}, "Not a valid ISBN."),
        ({"authors": "x" * 256}, "Author names cannot be longer than 255 characters."),
        ({"tags": "x" * 65}, "Tags cannot be longer than 64 characters."),
        ({"publication_year": "abc"}, "Not a valid integer value."),
        ({"publication_year": "10000"}, "Number must be between 1 and 9999."),
        ({"copies": "0"}, "Number must be between 1 and 999."),
        ({"copies": ""}, "This field is required."),
        ({"language": "x" * 65}, "Field cannot be longer than 64 characters."),
        ({"notes": "x" * 4001}, "Field cannot be longer than 4000 characters."),
    ],
)
def test_add_book_rejects_invalid_input(db_app, client, fields, message):
    rv = _post(client, **fields)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert message in html
    assert _scalar(db_app, "SELECT COUNT(*) FROM BOOKS") == 0


def test_add_book_rejects_isbn_already_in_catalogue(db_app, client):
    _post(client, title="Dune", isbn="9780441172719")

    rv = _post(client, title="Dune again", isbn="0-441-17271-7")

    assert "A book with ISBN 9780441172719 is already in the catalogue." in (
        rv.get_data(as_text=True)
    )
    assert _scalar(db_app, "SELECT COUNT(*) FROM BOOKS") == 1


def test_invalid_add_without_htmx_shows_the_add_page(client):
    rv = client.post("/books/new", data={"title": "", "copies": "1"})
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" in html
    assert "This field is required." in html


# Model


@pytest.mark.parametrize(
    ("isbn", "expected"),
    [
        ("9780441172719", "9780441172719"),
        ("978-0-441-17271-9", "9780441172719"),
        ("0 441 17271 7", "9780441172719"),
        ("080442957X", "9780804429573"),
        ("080442957x", "9780804429573"),
    ],
)
def test_normalise_isbn(isbn, expected):
    assert normalise_isbn(isbn) == expected


@pytest.mark.parametrize(
    "isbn", ["", "abc", "9780441172718", "0441172718", "97804411727190", "X441172717"]
)
def test_normalise_isbn_rejects_invalid(isbn):
    with pytest.raises(InvalidIsbnError):
        normalise_isbn(isbn)


def test_add_book_model_returns_id_listed_in_catalogue(db_app, user):
    with db_app.app_context():
        conn = get_db()
        book_id = add_book(conn, user.id, NewBook(title="Dune", authors=("Herbert",)))
        books = list_books(conn).books

    assert [(b.id, b.title, b.authors) for b in books] == [
        (book_id, "Dune", ("Herbert",))
    ]
