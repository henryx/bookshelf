"""Looking a book up online by its ISBN to fill in the add-book form."""

import logging
import re

import pytest
import requests
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import (
    Author,
    BookLookupError,
    FoundBook,
    NewBook,
    add_book,
    look_up_isbn,
)
from bookshelf.models.users import User, create_user

HTMX = {"HX-Request": "true"}
DUNE_ISBN = "9780441172719"
DUNE = {
    "title": "Dune",
    "authors": ["Frank Herbert"],
    "publisher": "Ace",
    "publishedDate": "1990-09-01",
    "description": "<p>Set on the desert planet <b>Arrakis</b>&hellip;</p>",
    "categories": ["Fiction"],
}


# Model


def test_look_up_finds_the_book_in_google_books(online_catalogues):
    online_catalogues.google_has(**DUNE)

    found = look_up_isbn(DUNE_ISBN)

    assert found == FoundBook(
        source="Google Books",
        title="Dune",
        authors=(Author("Frank", "Herbert"),),
        publisher="Ace",
        publication_year=1990,
        plot="Set on the desert planet Arrakis…",
        categories=("Fiction",),
    )
    assert online_catalogues.calls == ["google"]


def test_look_up_falls_back_to_open_library(online_catalogues):
    online_catalogues.open_library_has(
        DUNE_ISBN,
        title="Dune",
        authors=[{"name": "Frank Herbert"}, {"name": "Homer"}],
        publishers=[{"name": "Chilton Books"}, {"name": "Ace"}],
        publish_date="August 1965",
        subjects=[{"name": "Large type books"}],
    )

    found = look_up_isbn(DUNE_ISBN)

    # Open Library has no plot, and its subjects are too loose for categories.
    assert found == FoundBook(
        source="Open Library",
        title="Dune",
        authors=(Author("Frank", "Herbert"), Author("", "Homer")),
        publisher="Chilton Books",
        publication_year=1965,
        plot=None,
        categories=(),
    )
    assert online_catalogues.calls == ["google", "open_library"]


@pytest.mark.parametrize(
    ("given", "author"),
    [
        ("Frank Herbert", Author("Frank", "Herbert")),
        ("Ursula K. Le Guin", Author("Ursula K. Le", "Guin")),
        ("Herbert, Frank", Author("Frank", "Herbert")),
        ("Homer", Author("", "Homer")),
        ("  Italo   Calvino ", Author("Italo", "Calvino")),
    ],
)
def test_look_up_splits_authors_into_name_and_surname(online_catalogues, given, author):
    """At the last space, or at a comma ("Surname, Name"); one word is a surname."""
    online_catalogues.google_has(title="Dune", authors=[given])

    found = look_up_isbn(DUNE_ISBN)

    assert found is not None
    assert found.authors == (author,)


def test_look_up_of_a_book_with_little_data(online_catalogues):
    online_catalogues.google_has(title="Dune", publishedDate="sometime")

    found = look_up_isbn(DUNE_ISBN)

    assert found == FoundBook(
        source="Google Books",
        title="Dune",
        authors=(),
        publisher=None,
        publication_year=None,
        plot=None,
        categories=(),
    )


def test_look_up_sends_the_google_books_api_key_in_a_header(online_catalogues):
    """Not in the URL, which a failed request's error (logged) would show."""
    online_catalogues.google = 503
    online_catalogues.open_library_has(DUNE_ISBN, title="Dune")

    look_up_isbn(DUNE_ISBN, google_books_key="k3y")

    google, open_library = online_catalogues.headers
    assert google["X-goog-api-key"] == "k3y"
    assert "X-goog-api-key" not in open_library


def test_look_up_without_an_api_key(online_catalogues):
    look_up_isbn(DUNE_ISBN)

    assert all("X-goog-api-key" not in sent for sent in online_catalogues.headers)


def test_look_up_of_a_book_neither_has(online_catalogues):
    assert look_up_isbn(DUNE_ISBN) is None
    assert online_catalogues.calls == ["google", "open_library"]


@pytest.mark.parametrize(
    "failure",
    [429, 503, requests.ConnectionError("no network"), requests.Timeout("slow")],
    ids=["quota", "unavailable", "unreachable", "timeout"],
)
def test_look_up_asks_open_library_when_google_fails(
    online_catalogues, caplog, failure
):
    online_catalogues.google = failure
    online_catalogues.open_library_has(DUNE_ISBN, title="Dune")

    with caplog.at_level(logging.WARNING):
        found = look_up_isbn(DUNE_ISBN)

    assert found is not None and found.source == "Open Library"
    assert f"Looking up ISBN {DUNE_ISBN} in Google Books failed" in caplog.messages


def test_look_up_says_when_a_service_failed_and_the_other_has_nothing(
    online_catalogues,
):
    """Not "not found": the one that failed may have it."""
    online_catalogues.google = requests.ConnectionError("no network")

    with pytest.raises(BookLookupError, match="Could not reach Google Books"):
        look_up_isbn(DUNE_ISBN)


def test_look_up_when_both_fail(online_catalogues):
    online_catalogues.google = 503
    online_catalogues.open_library = requests.Timeout("slow")

    with pytest.raises(
        BookLookupError, match="Could not reach Google Books or Open Library"
    ):
        look_up_isbn(DUNE_ISBN)


# The form's Look up button


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


def _look_up(client: FlaskClient, headers: dict[str, str] = HTMX, **fields: str):
    data = {"title": "", "isbn": DUNE_ISBN, "copies": "1", "lookup": "1", **fields}
    return client.post("/books/new", data=data, headers=headers)


def _value(html: str, name: str) -> str | None:
    tag = re.search(rf'<input [^>]*name="{name}"[^>]*>', html)
    assert tag, name
    value = re.search(r'value="([^"]*)"', tag[0])
    return value[1] if value else None


def test_add_form_has_a_look_up_button(client):
    html = client.get("/books/new", headers=HTMX).get_data(as_text=True)

    button = re.search(r'<button [^>]*name="lookup"[^>]*>', html)
    assert button
    assert 'hx-post="/books/new"' in button[0]
    assert 'hx-target="#add-book-form"' in button[0]
    assert 'hx-indicator="#add-book-lookup"' in button[0]
    assert "formnovalidate" in button[0]


def test_a_picked_book_has_no_look_up_button(db_app: Flask, client, password):
    with db_app.app_context():
        other = create_user(get_db(), "other@example.com", password)
        book_id = add_book(get_db(), other.id, NewBook("Dune", isbn=DUNE_ISBN))

    html = client.get(f"/books/new?book={book_id}", headers=HTMX)

    assert 'name="lookup"' not in html.get_data(as_text=True)


def test_look_up_fills_the_empty_fields(client, online_catalogues):
    online_catalogues.google_has(**DUNE)

    rv = _look_up(client)

    html = rv.get_data(as_text=True)
    assert rv.status_code == 200
    assert _value(html, "title") == "Dune"
    assert _value(html, "authors-0-name") == "Frank"
    assert _value(html, "authors-0-surname") == "Herbert"
    assert _value(html, "publisher") == "Ace"
    assert _value(html, "publication_year") == "1990"
    assert _value(html, "categories-0") == "Fiction"
    assert "Set on the desert planet Arrakis…</textarea>" in html
    assert (
        "Filled in title, authors, publisher, year, plot and categories "
        "from Google Books." in html
    )
    assert 'class="error"' not in html


def test_look_up_keeps_what_was_typed(client, online_catalogues):
    online_catalogues.google_has(**DUNE)

    html = _look_up(
        client,
        title="Dune (40th anniversary)",
        publication_year="2005",
        **{"authors-0-name": "", "authors-0-surname": "Herbert"},
        **{"categories-0": "Science fiction"},
    ).get_data(as_text=True)

    assert _value(html, "title") == "Dune (40th anniversary)"
    assert _value(html, "publication_year") == "2005"
    assert _value(html, "authors-0-surname") == "Herbert"
    assert _value(html, "authors-0-name") in ("", None)
    assert _value(html, "categories-0") == "Science fiction"
    assert _value(html, "publisher") == "Ace"
    assert "Filled in publisher and plot from Google Books." in html


def test_look_up_with_every_field_filled(client, online_catalogues):
    online_catalogues.google_has(title="Dune")

    html = _look_up(client, title="Dune").get_data(as_text=True)

    assert "Google Books has this book, but nothing to add to it." in html


def test_look_up_without_javascript_shows_the_page(client, online_catalogues):
    online_catalogues.google_has(**DUNE)

    html = _look_up(client, headers={}).get_data(as_text=True)

    assert "<!doctype html>" in html
    assert _value(html, "title") == "Dune"


@pytest.mark.parametrize("isbn", ["", "123"])
def test_look_up_needs_a_valid_isbn(client, online_catalogues, isbn):
    html = _look_up(client, isbn=isbn).get_data(as_text=True)

    assert "Enter a valid ISBN to look it up." in html
    assert online_catalogues.calls == []


def test_look_up_of_a_book_in_the_catalogue_offers_it(
    db_app: Flask, client, online_catalogues, password
):
    """No need to ask online: the catalogue has it."""
    with db_app.app_context():
        other = create_user(get_db(), "other@example.com", password)
        add_book(get_db(), other.id, NewBook("Dune", isbn=DUNE_ISBN))

    html = _look_up(client, isbn="0-441-17271-7").get_data(as_text=True)

    assert "Add “Dune” to your collection" in html
    assert online_catalogues.calls == []


def test_look_up_of_a_book_not_found(client, online_catalogues, user, caplog):
    with caplog.at_level(logging.INFO):
        html = _look_up(client).get_data(as_text=True)

    assert f"No book found online with ISBN {DUNE_ISBN}." in html
    assert f"ISBN {DUNE_ISBN} looked up by {user.email}: not found" in caplog.messages


def test_look_up_when_the_services_cannot_be_reached(client, online_catalogues):
    online_catalogues.google = requests.ConnectionError("no network")
    online_catalogues.open_library = requests.ConnectionError("no network")

    html = _look_up(client).get_data(as_text=True)

    assert (
        f"Could not reach Google Books or Open Library to look up ISBN {DUNE_ISBN}. "
        "Try again later, or fill in the book yourself." in html
    )


def test_look_up_uses_the_configured_api_key(
    db_app: Flask, client, online_catalogues, monkeypatch
):
    monkeypatch.setitem(db_app.config, "GOOGLE_BOOKS_API_KEY", "k3y")
    online_catalogues.google_has(**DUNE)

    _look_up(client)

    assert online_catalogues.headers[0]["X-goog-api-key"] == "k3y"


def test_look_up_saves_nothing(db_app: Flask, client, online_catalogues):
    online_catalogues.google_has(**DUNE)

    _look_up(client)

    with db_app.app_context(), get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM BOOKS")
        assert cur.fetchone() == (0,)


def test_look_up_logs_what_it_found(client, online_catalogues, user, caplog):
    online_catalogues.google_has(**DUNE)

    with caplog.at_level(logging.INFO):
        _look_up(client)

    assert (
        f"ISBN {DUNE_ISBN} looked up by {user.email}: found in Google Books"
        in caplog.messages
    )
