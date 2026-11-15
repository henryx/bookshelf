"""Editing a book: every field but the title; its ISBN while it has none, or by an admin."""

import logging
import re
import threading
import time
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from firebird.driver import Connection
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import (
    Author,
    BookDetails,
    BookExistsError,
    InvalidIsbnError,
    IsbnAlreadySetError,
    NewBook,
    UnknownBookError,
    add_book,
    find_book_details,
    update_book,
)
from bookshelf.models.users import Role, User, create_user

HTMX = {"HX-Request": "true"}
DUNE_ISBN = "9780441172719"
EMMA_ISBN = "9780141439587"


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


def _book(db_app: Flask, user: User, book: NewBook) -> UUID:
    with db_app.app_context():
        return add_book(get_db(), user.id, book)


def _details(db_app: Flask, book_id: UUID) -> BookDetails:
    with db_app.app_context():
        details = find_book_details(get_db(), book_id)
    assert details is not None
    return details


def _update(
    db_app: Flask,
    book_id: UUID,
    *,
    authors: tuple[Author, ...] = (),
    publisher: str | None = None,
    publication_year: int | None = None,
    series: str | None = None,
    volume: str | None = None,
    plot: str | None = None,
    categories: tuple[str, ...] = (),
    isbn: str | None = None,
    replace_isbn: bool = False,
) -> None:
    with db_app.app_context():
        update_book(
            get_db(),
            book_id,
            authors=authors,
            publisher=publisher,
            publication_year=publication_year,
            series=series,
            volume=volume,
            plot=plot,
            categories=categories,
            isbn=isbn,
            replace_isbn=replace_isbn,
        )


DUNE = NewBook(
    "Dune",
    series="Dune Chronicles",
    authors=(Author("Frank", "Herbert"), Author("", "Anonymous")),
    publisher="Ace",
    publication_year=1965,
    volume="1",
    plot="First of six.",
    categories=("sf", "Classic"),
)


# Model


def test_find_book_details_has_every_field(db_app, user):
    book_id = _book(db_app, user, replace(DUNE, isbn=DUNE_ISBN))

    assert _details(db_app, book_id) == BookDetails(
        id=book_id,
        title="Dune",
        authors=(Author("Frank", "Herbert"), Author("", "Anonymous")),
        isbn=DUNE_ISBN,
        publisher="Ace",
        publication_year=1965,
        series="Dune Chronicles",
        volume="1",
        plot="First of six.",
        categories=("Classic", "sf"),
        collections=1,
    )


def test_find_book_details_of_an_unknown_book(db_app):
    with db_app.app_context():
        assert find_book_details(get_db(), uuid4()) is None


def test_update_book_saves_volume_plot_and_categories(db_app, user):
    book_id = _book(db_app, user, DUNE)

    _update(
        db_app,
        book_id,
        volume="II",
        plot="Signed.",
        categories=(" new ", "", "NEW", "x"),
    )

    details = _details(db_app, book_id)
    assert (details.volume, details.plot, details.categories) == (
        "II",
        "Signed.",
        ("new", "x"),
    )
    assert (details.title, details.isbn) == ("Dune", None)  # never changed here


def test_update_book_empties_fields(db_app, user):
    book_id = _book(db_app, user, DUNE)

    _update(db_app, book_id)

    details = _details(db_app, book_id)
    assert (details.volume, details.plot, details.categories) == (None, None, ())
    assert (details.authors, details.publisher, details.publication_year) == (
        (),
        None,
        None,
    )


def test_update_book_saves_authors_publisher_and_year(db_app, user):
    book_id = _book(db_app, user, DUNE)

    _update(
        db_app,
        book_id,
        authors=(
            Author("", "Anonymous"),
            Author(" Frank ", "Herbert"),
            Author("FRANK", "HERBERT"),  # a repeat, ignoring case
            Author("Brian", "Herbert"),
        ),
        publisher="Chilton",
        publication_year=1966,
    )

    details = _details(db_app, book_id)
    assert details.authors == (
        Author("", "Anonymous"),
        Author("Frank", "Herbert"),
        Author("Brian", "Herbert"),
    )
    assert (details.publisher, details.publication_year) == ("Chilton", 1966)


def _author_names(db_app: Flask) -> list[str]:
    with db_app.app_context(), get_db().cursor() as cur:
        cur.execute("SELECT NAME || '/' || SURNAME FROM AUTHORS ORDER BY SURNAME, NAME")
        return [name for (name,) in cur.fetchall()]


def test_an_author_no_book_uses_any_more_is_deleted(db_app, user):
    book_id = _book(
        db_app, user, NewBook("Dune", authors=(Author("Frnak", "Herbert"),))
    )
    _book(db_app, user, NewBook("Emma", authors=(Author("Jane", "Austen"),)))
    other_id = _book(
        db_app, user, NewBook("Persuasion", authors=(Author("Jane", "Austen"),))
    )

    _update(db_app, book_id, authors=(Author("Frank", "Herbert"),))
    _update(db_app, other_id)  # Jane Austen is still Emma's author

    assert _author_names(db_app) == ["Jane/Austen", "Frank/Herbert"]


def test_an_author_being_linked_meanwhile_is_kept(
    db_app, user, other_conn: Connection, caplog
):
    book_id = _book(
        db_app, user, NewBook("Dune", authors=(Author("Frank", "Herbert"),))
    )
    other_id = _book(db_app, user, NewBook("Children of Dune"))
    errors: list[Exception] = []

    def remove_author() -> None:
        try:
            _update(db_app, book_id)
        except Exception as e:  # noqa: BLE001 - reported by the assertion below
            errors.append(e)

    # Another request gives the author a second book, not yet committed...
    with other_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) "
            "SELECT ?, ID, 1 FROM AUTHORS",
            (other_id.bytes,),
        )
    # ...while this one removes the author from Dune.
    thread = threading.Thread(target=remove_author)
    with caplog.at_level(logging.INFO):
        thread.start()
        time.sleep(0.5)
        other_conn.commit()
        thread.join(timeout=30)

    assert not thread.is_alive()
    assert errors == []
    assert _details(db_app, book_id).authors == ()
    assert _details(db_app, other_id).authors == (Author("Frank", "Herbert"),)
    assert "kept: a book being saved at this moment uses it" in caplog.text


def test_update_book_sets_an_isbn_once(db_app, user):
    book_id = _book(db_app, user, DUNE)

    _update(db_app, book_id, isbn="0-441-17271-7")
    assert _details(db_app, book_id).isbn == DUNE_ISBN

    with pytest.raises(IsbnAlreadySetError):
        _update(db_app, book_id, volume="changed", isbn=EMMA_ISBN)
    details = _details(db_app, book_id)
    assert (details.isbn, details.volume) == (DUNE_ISBN, None)


def test_update_book_refuses_an_isbn_in_the_catalogue(db_app, user):
    _book(db_app, user, NewBook("Emma", isbn=EMMA_ISBN))
    book_id = _book(db_app, user, DUNE)

    with pytest.raises(BookExistsError, match=EMMA_ISBN):
        _update(db_app, book_id, volume="changed", isbn=EMMA_ISBN)
    assert _details(db_app, book_id).volume == "1"


def test_update_book_refuses_an_invalid_isbn(db_app, user):
    book_id = _book(db_app, user, DUNE)

    with pytest.raises(InvalidIsbnError):
        _update(db_app, book_id, isbn="12345")


def test_update_book_replaces_or_removes_the_isbn(db_app, user):
    book_id = _book(db_app, user, NewBook("Dune", isbn=EMMA_ISBN))

    _update(db_app, book_id, isbn="0-441-17271-7", replace_isbn=True)
    assert _details(db_app, book_id).isbn == DUNE_ISBN

    _update(db_app, book_id, replace_isbn=True)
    assert _details(db_app, book_id).isbn is None


def test_replaced_isbn_must_not_be_another_books(db_app, user):
    _book(db_app, user, NewBook("Emma", isbn=EMMA_ISBN))
    book_id = _book(db_app, user, NewBook("Dune", isbn=DUNE_ISBN))

    with pytest.raises(BookExistsError, match=EMMA_ISBN):
        _update(db_app, book_id, isbn=EMMA_ISBN, replace_isbn=True)
    assert _details(db_app, book_id).isbn == DUNE_ISBN


def test_update_an_unknown_book(db_app):
    with pytest.raises(UnknownBookError):
        _update(db_app, uuid4(), volume="1")


# Routes


def _url(book_id: UUID) -> str:
    return f"/books/{book_id}"


def _form(**fields: str) -> dict[str, str]:
    """DUNE as its edit form posts it, with `fields` changed."""
    return {
        "authors-0-name": "Frank",
        "authors-0-surname": "Herbert",
        "authors-1-name": "",
        "authors-1-surname": "Anonymous",
        "publisher": "Ace",
        "publication_year": "1965",
        "series": "Dune Chronicles",
        "volume": "1",
        "plot": "First of six.",
        "categories-0": "Classic",
        "categories-1": "sf",
        **fields,
    }


def test_book_details_for_the_dialog(db_app, client, user):
    book_id = _book(db_app, user, DUNE)

    html = client.get(_url(book_id), headers=HTMX).get_data(as_text=True)

    assert "<html" not in html
    assert "Dune" in html
    assert 'name="title"' not in html  # shown, not editable
    assert re.search(r'<input id="edit-book-authors-0-name" [^>]*value="Frank"', html)
    assert re.search(
        r'<input id="edit-book-authors-1-surname" [^>]*value="Anonymous"', html
    )
    assert re.search(r'<input [^>]*name="publisher"[^>]*value="Ace"', html)
    assert re.search(r'<input [^>]*name="publication_year"[^>]*value="1965"', html)
    assert re.search(r'<input [^>]*name="volume"[^>]*value="1"', html)
    assert re.search(r'<input id="edit-book-categories-0" [^>]*value="Classic"', html)
    assert re.search(r'<input id="edit-book-categories-1" [^>]*value="sf"', html)
    assert 'name="add_category"' in html
    assert "First of six." in html
    assert 'name="isbn"' in html


def test_book_page_without_javascript(db_app, client, user):
    book_id = _book(db_app, user, DUNE)

    html = client.get(_url(book_id)).get_data(as_text=True)

    assert "<html" in html
    assert f'action="{_url(book_id)}"' in html


def test_isbn_is_shown_not_editable_once_set(db_app, client, user):
    book_id = _book(db_app, user, NewBook("Dune", isbn=DUNE_ISBN))

    html = client.get(_url(book_id), headers=HTMX).get_data(as_text=True)

    assert DUNE_ISBN in html
    assert 'name="isbn"' not in html


def test_save_redirects_to_the_list_it_came_from(db_app, client, user):
    book_id = _book(db_app, user, DUNE)
    data = {
        "volume": "2",
        "categories-0": "a",
        "categories-1": "b",
        "plot": "",
        "q": "dune",
        "page": "1",
    }

    rv = client.post(_url(book_id), data=data, headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/?q=dune"
    details = _details(db_app, book_id)
    assert (details.volume, details.plot, details.categories) == ("2", None, ("a", "b"))
    assert "Book “Dune” saved." in client.get("/").get_data(as_text=True)


def test_save_from_the_collection_returns_there(db_app, client, user):
    book_id = _book(db_app, user, DUNE)
    data = {"volume": "2", "list": "collection", "sort": "author", "page": "2"}

    rv = client.post(_url(book_id), data=data)

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/collection/?sort=author&page=2"


def test_save_goes_back_only_to_a_book_list(db_app, client, user):
    book_id = _book(db_app, user, DUNE)

    rv = client.post(_url(book_id), data={"list": "https://evil.example"})

    assert rv.headers["Location"] == "/"


def test_save_sets_the_isbn(db_app, client, user):
    book_id = _book(db_app, user, DUNE)

    client.post(_url(book_id), data={"isbn": "0-441-17271-7"})

    assert _details(db_app, book_id).isbn == DUNE_ISBN


def test_posted_isbn_is_ignored_once_the_book_has_one(db_app, client, user):
    book_id = _book(db_app, user, NewBook("Dune", isbn=DUNE_ISBN))

    rv = client.post(_url(book_id), data={"isbn": EMMA_ISBN, "volume": "3"})

    assert rv.status_code == 302
    details = _details(db_app, book_id)
    assert (details.isbn, details.volume) == (DUNE_ISBN, "3")


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"isbn": "12345"}, "Not a valid ISBN."),
        ({"volume": "x" * 33}, "32 characters"),
        ({"categories-0": "y" * 65}, "64 characters"),
    ],
)
def test_save_with_errors_shows_the_form_again(db_app, client, user, data, error):
    book_id = _book(db_app, user, DUNE)

    rv = client.post(_url(book_id), data={"volume": "changed", **data}, headers=HTMX)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert 'id="edit-book-form"' in html
    assert error in html
    assert _details(db_app, book_id).volume == "1"


def test_save_refuses_an_isbn_in_the_catalogue(db_app, client, user):
    _book(db_app, user, NewBook("Emma", isbn=EMMA_ISBN))
    book_id = _book(db_app, user, DUNE)

    rv = client.post(_url(book_id), data={"isbn": EMMA_ISBN}, headers=HTMX)

    assert f"A book with ISBN {EMMA_ISBN} is already in the catalogue." in (
        rv.get_data(as_text=True)
    )
    assert _details(db_app, book_id).isbn is None


def test_save_refuses_an_isbn_set_meanwhile(
    db_app, client, user, monkeypatch: pytest.MonkeyPatch
):
    """The form had no ISBN yet; someone else set one before this save."""
    book_id = _book(db_app, user, DUNE)
    _update(db_app, book_id, volume="1", isbn=DUNE_ISBN)
    stale = BookDetails(id=book_id, title="Dune")
    monkeypatch.setattr("bookshelf.routes.main.find_book_details", lambda *_args: stale)

    rv = client.post(_url(book_id), data={"isbn": EMMA_ISBN}, headers=HTMX)

    assert "This book already has an ISBN." in rv.get_data(as_text=True)
    assert _details(db_app, book_id).isbn == DUNE_ISBN


def test_save_of_a_book_deleted_meanwhile_is_not_found(
    db_app, client, user, monkeypatch: pytest.MonkeyPatch, caplog
):
    """The dialog was opened, then an admin deleted the book."""
    gone = BookDetails(id=uuid4(), title="Gone")
    monkeypatch.setattr("bookshelf.routes.main.find_book_details", lambda *_args: gone)

    with caplog.at_level(logging.INFO):
        rv = client.post(_url(gone.id), data={"volume": "2"})

    assert rv.status_code == 404
    assert f"Book {gone.id} deleted before {user.email} saved it" in caplog.text


@pytest.mark.parametrize("method", ["get", "post"])
def test_unknown_book_is_not_found(client, method):
    assert getattr(client, method)(_url(uuid4())).status_code == 404


def test_book_needs_login(db_app, db_client, user):
    book_id = _book(db_app, user, DUNE)

    rv = db_client.get(_url(book_id))

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_save_is_logged_with_the_changed_fields(db_app, client, user, caplog):
    book_id = _book(db_app, user, DUNE)

    with caplog.at_level(logging.INFO):
        data = _form(publication_year="1966", **{"categories-1": "new"})
        client.post(_url(book_id), data=data)

    assert f"Book 'Dune' edited by {user.email}: year, categories" in caplog.text


def test_unchanged_save_is_logged_as_such(db_app, client, user, caplog):
    book_id = _book(db_app, user, DUNE)

    with caplog.at_level(logging.INFO):
        client.post(_url(book_id), data=_form())

    assert f"Book 'Dune' edited by {user.email}: no changes" in caplog.text


def test_save_authors_publisher_and_year(db_app, client, user):
    book_id = _book(db_app, user, DUNE)
    data = _form(publisher="Chilton", publication_year="1966")
    data.update({"authors-0-name": "Brian", "authors-1-name": "Frank"})
    data["authors-1-surname"] = "Herbert"

    rv = client.post(_url(book_id), data=data)

    assert rv.status_code == 302
    details = _details(db_app, book_id)
    assert details.authors == (Author("Brian", "Herbert"), Author("Frank", "Herbert"))
    assert (details.publisher, details.publication_year) == ("Chilton", 1966)


@pytest.mark.parametrize(
    ("authors", "error"),
    [
        ({"authors-1-surname": ""}, "Surname is required."),
        ({"authors-1-name": "Frank", "authors-1-surname": "Herbert"}, "already listed"),
    ],
)
def test_save_with_author_errors_shows_the_form_again(
    db_app, client, user, authors, error
):
    book_id = _book(db_app, user, DUNE)
    data = {**_form(volume="changed"), **authors}
    data["authors-1-name"] = data.get("authors-1-name") or "Someone"

    rv = client.post(_url(book_id), data=data, headers=HTMX)

    assert error in rv.get_data(as_text=True)
    assert _details(db_app, book_id).volume == "1"


@pytest.mark.parametrize(
    ("button", "rows"),
    [({"add_author": "1"}, ["0", "1", "2"]), ({"remove_author": "1"}, ["0"])],
)
def test_author_rows_without_javascript(db_app, client, user, button, rows):
    book_id = _book(db_app, user, DUNE)

    rv = client.post(_url(book_id), data={**_form(volume="changed"), **button})
    html = rv.get_data(as_text=True)

    assert re.findall(r'name="authors-(\d+)-surname"', html) == rows
    assert _details(db_app, book_id).volume == "1"  # not saved


@pytest.mark.parametrize(
    ("list_url", "book_url"),
    [
        ("/?q=dun", "?q=dun"),
        ("/collection/?sort=added", "?list=collection&amp;sort=added"),
    ],
)
def test_list_titles_open_the_book(db_app, client, user, list_url, book_url):
    book_id = _book(db_app, user, DUNE)

    html = client.get(list_url).get_data(as_text=True)

    url = f"{_url(book_id)}{book_url}"
    assert f'<a class="book-title" href="{url}" hx-get="{url}"' in html
    assert 'hx-target="#book-dialog-body"' in html
    assert '<dialog id="book-dialog"' in html


# Admins correct a wrong ISBN


@pytest.fixture
def admin_client(db_app: Flask, db_client: FlaskClient, password: str) -> FlaskClient:
    with db_app.app_context():
        create_user(get_db(), "boss@example.com", password, role=Role.ADMIN)
    rv = db_client.post(
        "/login", data={"email": "boss@example.com", "password": password}
    )
    assert rv.status_code == 302
    return db_client


def test_admin_can_edit_a_set_isbn(db_app, admin_client, user):
    book_id = _book(db_app, user, NewBook("Dune", isbn=EMMA_ISBN))

    html = admin_client.get(_url(book_id), headers=HTMX).get_data(as_text=True)

    assert re.search(rf'<input [^>]*name="isbn"[^>]*value="{EMMA_ISBN}"', html)
    assert "Only admins can change or remove it." in html


def test_admin_corrects_a_wrong_isbn(db_app, admin_client, user, caplog):
    book_id = _book(db_app, user, NewBook("Dune", isbn=EMMA_ISBN))

    with caplog.at_level(logging.INFO):
        rv = admin_client.post(_url(book_id), data={"isbn": "0-441-17271-7"})

    assert rv.status_code == 302
    assert _details(db_app, book_id).isbn == DUNE_ISBN
    assert f"ISBN {EMMA_ISBN} -> {DUNE_ISBN}" in caplog.text


def test_admin_removes_an_isbn(db_app, admin_client, user, caplog):
    book_id = _book(db_app, user, NewBook("Dune", isbn=EMMA_ISBN))

    with caplog.at_level(logging.INFO):
        admin_client.post(_url(book_id), data={"isbn": ""})

    assert _details(db_app, book_id).isbn is None
    assert f"ISBN {EMMA_ISBN} -> none" in caplog.text


def test_admin_save_keeping_the_isbn_leaves_it(db_app, admin_client, user, caplog):
    book_id = _book(db_app, user, NewBook("Dune", isbn=DUNE_ISBN))

    with caplog.at_level(logging.INFO):
        admin_client.post(_url(book_id), data={"isbn": "978-0-441-17271-9"})

    assert _details(db_app, book_id).isbn == DUNE_ISBN
    assert "Book 'Dune' edited by boss@example.com: no changes" in caplog.text


def test_admin_cannot_take_another_books_isbn(db_app, admin_client, user):
    _book(db_app, user, NewBook("Emma", isbn=EMMA_ISBN))
    book_id = _book(db_app, user, NewBook("Dune", isbn=DUNE_ISBN))

    rv = admin_client.post(_url(book_id), data={"isbn": EMMA_ISBN}, headers=HTMX)

    assert f"A book with ISBN {EMMA_ISBN} is already in the catalogue." in (
        rv.get_data(as_text=True)
    )
    assert _details(db_app, book_id).isbn == DUNE_ISBN


def test_admin_isbn_is_validated(db_app, admin_client, user):
    book_id = _book(db_app, user, NewBook("Dune", isbn=DUNE_ISBN))

    rv = admin_client.post(_url(book_id), data={"isbn": "12345"}, headers=HTMX)

    assert "Not a valid ISBN." in rv.get_data(as_text=True)
    assert _details(db_app, book_id).isbn == DUNE_ISBN


def test_update_book_saves_and_empties_the_series(db_app, user):
    book_id = _book(db_app, user, DUNE)

    _update(db_app, book_id, series="Children of Dune")
    assert _details(db_app, book_id).series == "Children of Dune"

    _update(db_app, book_id)
    assert _details(db_app, book_id).series is None


def test_edit_form_has_series_beside_volume_and_plot(db_app, client, user):
    book_id = _book(db_app, user, DUNE)

    html = client.get(_url(book_id), headers=HTMX).get_data(as_text=True)

    one_row = r'<div class="field-row">(?:(?!field-row).)*name="series"[^>]*value="Dune Chronicles"(?:(?!field-row).)*name="volume"'
    assert re.search(one_row, html, re.DOTALL)
    assert '<label for="edit-book-plot">Plot</label>' in html


def test_series_change_is_logged(db_app, client, user, caplog):
    book_id = _book(db_app, user, DUNE)

    with caplog.at_level(logging.INFO):
        client.post(_url(book_id), data=_form(series="Dune Saga"))

    assert f"Book 'Dune' edited by {user.email}: series" in caplog.text
