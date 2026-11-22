"""Picking a catalogue book in the add-book form: title and ISBN suggestions,
and the form for adding the picked book to the user's collection."""

import re
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import Author, NewBook, add_book, find_book
from bookshelf.models.users import User, create_user

HTMX = {"HX-Request": "true"}
DUNE_ISBN = "9780441172719"


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


@pytest.fixture
def other(db_app: Flask, password: str) -> User:
    with db_app.app_context():
        return create_user(get_db(), "other@example.com", password)


def _book(app: Flask, owner: User, book: NewBook, copies: int = 1) -> UUID:
    with app.app_context():
        return add_book(get_db(), owner.id, book, copies=copies)


@pytest.fixture
def dune(db_app: Flask, other: User) -> UUID:
    """A catalogue book in another user's collection only."""
    return _book(
        db_app,
        other,
        NewBook(
            "Dune",
            (Author("Frank", "Herbert"),),
            isbn=DUNE_ISBN,
            publisher="Chilton Books",
            publication_year=1965,
            series="Dune",
            volume="1",
            plot="Spice.",
            categories=("Sci-fi",),
        ),
    )


def _suggest(client: FlaskClient, field: str, q: str):
    return client.get(
        "/books/suggestions", query_string={"field": field, "q": q}, headers=HTMX
    )


def _options(html: str) -> list[tuple[str, str, str]]:
    """(value, data-book-id, details) of each option."""
    return re.findall(
        r'<li role="option" data-value="([^"]*)" data-book-id="([^"]*)">[^<]*'
        r'<span class="suggestion-detail">([^<]*)</span>',
        html,
    )


# Title suggestions


def test_title_suggestions_by_start_ignoring_case(db_app, client, other, dune):
    messiah = _book(db_app, other, NewBook("Dune Messiah"))
    _book(db_app, other, NewBook("Emma"))

    html = _suggest(client, "title", "DUN").get_data(as_text=True)

    assert _options(html) == [
        ("Dune", str(dune), "Frank Herbert · 1965"),
        ("Dune Messiah", str(messiah), ""),
    ]


def test_title_suggestions_do_not_match_inside_titles(client, dune):
    assert _options(_suggest(client, "title", "une").get_data(as_text=True)) == []


@pytest.mark.parametrize("q", ["", "   ", "x" * 256])
def test_no_title_suggestions_for_blank_or_too_long_text(client, dune, q):
    rv = _suggest(client, "title", q)

    assert rv.status_code == 200
    assert _options(rv.get_data(as_text=True)) == []


def test_title_suggestions_are_limited(db_app, client, other):
    for n in range(12):
        _book(db_app, other, NewBook(f"Book {n:02}"))

    assert len(_options(_suggest(client, "title", "book").get_data(as_text=True))) == 10


# ISBN suggestions


@pytest.mark.parametrize("q", ["978044", "978-0-441", " 9780441172719 ", "0441172717"])
def test_isbn_suggestions_by_start(client, dune, q):
    html = _suggest(client, "isbn", q).get_data(as_text=True)

    assert _options(html) == [(DUNE_ISBN, str(dune), "Dune · Frank Herbert")]


@pytest.mark.parametrize("q", ["", "441172719", "dune"])
def test_no_isbn_suggestions_for_other_text(client, dune, q):
    assert _options(_suggest(client, "isbn", q).get_data(as_text=True)) == []


def test_no_isbn_suggestions_for_text_too_long_for_an_isbn(client, dune):
    q = "978-0-441-17271-9" + "-" * 20

    assert _options(_suggest(client, "isbn", q).get_data(as_text=True)) == []


def test_book_suggestions_reject_unknown_field(client):
    assert _suggest(client, "publisher", "d").status_code == 400


def test_book_suggestions_require_login(db_client):
    assert db_client.get("/books/suggestions?field=title&q=d").status_code == 302


@pytest.mark.parametrize(("field", "name"), [("title", "title"), ("isbn", "isbn")])
def test_title_and_isbn_inputs_ask_for_suggestions(client, field, name):
    html = client.get("/books/new").get_data(as_text=True)

    input_tag = re.search(rf'<input [^>]*id="book-{name}"[^>]*>', html)
    assert input_tag
    assert 'role="combobox"' in input_tag[0]
    assert f'aria-controls="book-{name}-suggestions"' in input_tag[0]
    assert 'hx-get="/books/suggestions"' in input_tag[0]
    assert f'hx-vals="{{&#34;field&#34;: &#34;{field}&#34;}}"' in input_tag[0]
    assert 'hx-swap="innerHTML"' in input_tag[0]
    assert "data-book-pick" in input_tag[0]
    assert (
        f'<ul id="book-{name}-suggestions" class="suggestions" role="listbox"' in html
    )


def test_add_form_loads_a_picked_book_from_the_list_it_is_on(client):
    html = client.get("/books/new?list=collection").get_data(as_text=True)

    loader = re.search(r"<span [^>]*data-book-loader[^>]*>", html)
    assert loader
    assert 'hx-get="/books/new?list=collection"' in loader[0]
    assert 'hx-target="#add-book-form"' in loader[0]


# The form for a picked book


def _picked(client: FlaskClient, book_id: UUID | str, **params: str):
    return client.get(
        "/books/new", query_string={"book": str(book_id), **params}, headers=HTMX
    )


def _input(html: str, name: str) -> str:
    tag = re.search(rf'<(?:input|textarea) [^>]*name="{name}"[^>]*>', html)
    assert tag, name
    return tag[0]


def test_picked_book_fills_the_form_read_only(client, dune):
    html = _picked(client, dune).get_data(as_text=True)

    assert html.lstrip().startswith('<form id="add-book-form"')
    for name, value in [
        ("title", "Dune"),
        ("isbn", DUNE_ISBN),
        ("authors-0-name", "Frank"),
        ("authors-0-surname", "Herbert"),
        ("publisher", "Chilton Books"),
        ("publication_year", "1965"),
        ("series", "Dune"),
        ("volume", "1"),
        ("categories-0", "Sci-fi"),
    ]:
        tag = _input(html, name)
        assert f'value="{value}"' in tag, name
        assert "readonly" in tag, name
    assert "readonly" in _input(html, "plot")
    assert "Spice." in html
    assert "readonly" not in _input(html, "copies")
    # No rows to add or remove.
    assert 'name="add_author"' not in html
    assert 'name="add_category"' not in html


def test_picked_book_form_adds_it_to_the_collection(client, dune):
    html = _picked(client, dune).get_data(as_text=True)

    assert f'action="/collection/books/{dune}/add"' in html
    assert f'hx-post="/collection/books/{dune}/add"' in html
    assert re.search(
        r'<button type="submit"[^>]*>\s*Add to my collection\s*</button>', html
    )
    assert ">Add book</button>" not in html


def test_picked_book_form_offers_another_book(client, dune):
    html = _picked(client, dune, list="collection").get_data(as_text=True)

    assert '<input type="hidden" name="list" value="collection">' in html
    link = re.search(r"<a [^>]*>Choose another book</a>", html)
    assert link
    assert 'href="/books/new?list=collection"' in link[0]
    assert 'hx-get="/books/new?list=collection"' in link[0]
    assert 'hx-target="#add-book-form"' in link[0]


def test_posting_the_picked_book_form_adds_it(db_app, client, user, dune):
    form = {"title": "Dune", "isbn": DUNE_ISBN, "copies": "2", "list": "collection"}

    rv = client.post(f"/collection/books/{dune}/add", data=form, headers=HTMX)

    assert rv.headers["HX-Redirect"] == "/"
    with db_app.app_context():
        book = find_book(get_db(), user.id, dune)
    assert book is not None
    assert book.copies == 2


def test_picked_book_already_in_the_collection_says_so(db_app, client, user):
    emma = _book(db_app, user, NewBook("Emma"), copies=2)

    html = _picked(client, emma).get_data(as_text=True)

    assert "“Emma” is already in your collection (2 copies)." in " ".join(html.split())
    assert "Add to my collection" not in html
    assert 'name="copies"' not in html


def test_picked_book_without_htmx_is_the_add_page(client, dune):
    rv = client.get("/books/new", query_string={"book": str(dune)})
    html = rv.get_data(as_text=True)

    assert "<h2>Add book</h2>" in html
    assert f'action="/collection/books/{dune}/add"' in html


@pytest.mark.parametrize("book", [str(uuid4()), "nonsense"])
def test_picking_an_unknown_book_is_not_found(client, book):
    assert _picked(client, book).status_code == 404
