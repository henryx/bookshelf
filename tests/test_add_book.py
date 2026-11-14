"""Adding a book from the main page."""

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import (
    Author,
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


def _authors(*rows: tuple[str, str], start: int = 0) -> dict[str, str]:
    """Form fields for author rows of (name, surname), numbered from `start`."""
    fields = {}
    for index, (name, surname) in enumerate(rows, start=start):
        fields[f"authors-{index}-name"] = name
        fields[f"authors-{index}-surname"] = surname
    return fields


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
        **_authors(("Terry", "Pratchett"), ("", ""), ("  Neil ", " Gaiman  ")),
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
        "SELECT a.NAME, a.SURNAME FROM BOOK_AUTHORS ba "
        "JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID "
        "WHERE ba.BOOK_ID = ? ORDER BY ba.SORT_ORDER",
        (book_id,),
    )
    assert authors == [("Terry", "Pratchett"), ("Neil", "Gaiman")]
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
    _post(client, title="Mort", **_authors(("Terry", "Pratchett")))
    _post(client, title="Guards! Guards!", **_authors(("TERRY", "PRATCHETT")))

    names = _rows(db_app, "SELECT NAME, SURNAME FROM AUTHORS")
    assert names == [("Terry", "Pratchett")]


def test_add_book_author_with_surname_only(db_app, client):
    _post(client, title="Iliad", **_authors(("", "Homer")))

    names = _rows(db_app, "SELECT NAME, SURNAME FROM AUTHORS")
    assert names == [("", "Homer")]
    html = client.get("/").get_data(as_text=True)
    assert '<span class="book-authors">Homer</span>' in html


def test_add_book_keeps_author_rows_in_order_despite_gaps(db_app, client):
    """Rows removed in the browser leave gaps in the numbering."""
    _post(
        client,
        **_authors(("A", "First"), start=0),
        **_authors(("B", "Second"), start=3),
    )

    authors = _rows(
        db_app,
        "SELECT a.SURNAME FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID "
        "ORDER BY ba.SORT_ORDER",
    )
    assert authors == [("First",), ("Second",)]


def test_add_book_drops_repeated_author(db_app, client):
    _post(client, **_authors(("Ann", "Leckie"), ("ann", "LECKIE")))

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
        (_authors(("Ann", "")), "Surname is required."),
        (
            _authors(("x" * 128, "Leckie")),
            "Field cannot be longer than 127 characters.",
        ),
        (_authors(("Ann", "x" * 128)), "Field cannot be longer than 127 characters."),
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
        book_id = add_book(
            conn, user.id, NewBook(title="Dune", authors=(Author("Frank", "Herbert"),))
        )
        books = list_books(conn).books

    assert [(b.id, b.title, b.authors) for b in books] == [
        (book_id, "Dune", (Author("Frank", "Herbert"),))
    ]


# Author rows


def test_form_has_one_author_row_with_add_button_and_a_row_template(client):
    html = client.get("/books/new").get_data(as_text=True)

    assert 'name="authors-0-name"' in html
    assert 'name="authors-0-surname"' in html
    assert 'name="add_author"' in html
    assert "<template" in html
    assert 'name="authors-__index__-surname"' in html


def test_add_author_row_without_javascript(db_app, client):
    """The + button posts the form back with one more, empty, row."""
    rv = client.post(
        "/books/new",
        data={
            "title": "Dune",
            "copies": "1",
            "add_author": "1",
            **_authors(("Frank", "Herbert")),
        },
    )
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert 'value="Herbert"' in html
    assert 'name="authors-1-surname"' in html
    assert 'name="remove_author" value="1"' in html
    assert "This field is required." not in html
    assert _scalar(db_app, "SELECT COUNT(*) FROM BOOKS") == 0


def test_remove_author_row_without_javascript(db_app, client):
    rv = client.post(
        "/books/new",
        data={
            "title": "Good Omens",
            "copies": "1",
            "remove_author": "1",
            **_authors(("Terry", "Pratchett"), ("Neil", "Gaiman")),
        },
    )
    html = rv.get_data(as_text=True)

    assert 'value="Pratchett"' in html
    assert 'value="Gaiman"' not in html
    assert 'name="authors-1-surname"' not in html
    assert _scalar(db_app, "SELECT COUNT(*) FROM BOOKS") == 0
