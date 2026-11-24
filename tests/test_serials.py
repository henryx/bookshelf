"""Serials (magazines, journals) by their ISSN, in the ISBN field: see test_issn.py."""

import re
from uuid import UUID

import pytest
from flask import Flask
from flask.testing import FlaskClient
from raw_db import raw

from bookshelf.db import get_session
from bookshelf.models.collection_csv import import_collection
from bookshelf.models.users import User

HTMX = {"HX-Request": "true"}
NATURE = "9770317847001"  # ISSN 0317-8471, variant 00


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


def _add(client: FlaskClient, title: str, code: str):
    data = {"title": title, "isbn": code, "copies": "1"}
    return client.post("/books/new", data=data, headers=HTMX)


def _codes(app: Flask) -> list[object]:
    with app.app_context(), raw(get_session()).cursor() as cur:
        cur.execute("SELECT ISBN FROM BOOKS ORDER BY ISBN")
        return [code for (code,) in cur.fetchall()]


def _book_id(app: Flask, code: str) -> UUID:
    with app.app_context(), raw(get_session()).cursor() as cur:
        cur.execute("SELECT ID FROM BOOKS WHERE ISBN = ?", (code,))
        row = cur.fetchone()
    assert row is not None
    assert isinstance(row[0], UUID)
    return row[0]


def _titles(html: str) -> list[str]:
    return re.findall(r'<a class="book-title"[^>]*>([^<]*)</a>', html)


def test_a_serial_is_added_by_its_issn_and_issue(db_app, client):
    rv = _add(client, "Nature, May", "0317-8471 05")

    assert rv.status_code in (200, 204)
    assert _codes(db_app) == [f"{NATURE}05"]


def test_each_issue_is_a_catalogue_item_of_its_own(db_app, client):
    _add(client, "Nature, May", "0317-8471 05")

    june = _add(client, "Nature, June", "0317-8471 06")
    again = _add(client, "Nature, May again", f"{NATURE} 05")

    assert _codes(db_app) == [f"{NATURE}05", f"{NATURE}06"]
    assert june.status_code in (200, 204)
    assert "A book with ISSN 0317-8471 · issue 05 is already in the catalogue." in (
        again.get_data(as_text=True)
    )


def test_the_book_dialog_shows_the_issn_and_issue(db_app, client):
    _add(client, "Nature, May", "0317-8471 05")
    book_id = _book_id(db_app, f"{NATURE}05")

    html = client.get(f"/books/{book_id}", headers=HTMX).get_data(as_text=True)

    assert "<dt>ISSN</dt>" in html
    assert "0317-8471 · issue 05" in html


@pytest.mark.parametrize("search", ["0317-8471", "03178471", "9770317847"])
def test_the_lists_find_a_serial_by_its_issn(db_app, client, search):
    _add(client, "Nature, May", "0317-8471 05")
    _add(client, "Dune", "9780441172719")

    html = client.get("/catalogue", query_string={"q": search}).get_data(as_text=True)

    assert _titles(html) == ["Nature, May"]


def test_isbn_suggestions_show_a_serial_as_issn_and_issue(db_app, client):
    _add(client, "Nature, May", "0317-8471 05")

    html = client.get(
        "/books/suggestions", query_string={"field": "isbn", "q": "0317-8471"}
    ).get_data(as_text=True)

    assert f'data-value="{NATURE}05"' in html
    assert "0317-8471 · issue 05" in html


def test_look_up_is_for_isbns(db_app, client, online_catalogues):
    data = {"title": "", "isbn": "0317-8471 05", "copies": "1", "lookup": "1"}

    rv = client.post("/books/new", data=data, headers=HTMX)

    assert "Look up finds books by their ISBN" in rv.get_data(as_text=True)
    assert online_catalogues.calls == []


def test_a_csv_row_may_have_an_issn(db_app, user):
    text = "title,isbn,copies\nNature,0317-8471 05,1\n"

    with db_app.app_context():
        report = import_collection(get_session(), user.id, text)

    assert (report.added, report.errors) == (1, [])
    assert _codes(db_app) == [f"{NATURE}05"]
