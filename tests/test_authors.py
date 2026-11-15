"""The authors page: listing authors, and renaming one for every book (merging
it into another author with that name)."""

import logging
import re
import threading
import time
from uuid import UUID, uuid4

import pytest
from firebird.driver import Connection
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.authors import (
    AuthorEntry,
    Renamed,
    UnknownAuthorError,
    find_author,
    list_authors,
    rename_author,
)
from bookshelf.models.books import (
    SEARCH_MAX_LENGTH,
    Author,
    NewBook,
    add_book,
    find_book_details,
)
from bookshelf.models.users import User

HTMX = {"HX-Request": "true"}
FRNAK = Author("Frnak", "Herbert")
FRANK = Author("Frank", "Herbert")
JANE = Author("Jane", "Austen")


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


def _book(db_app: Flask, user: User, title: str, *authors: Author) -> UUID:
    with db_app.app_context():
        return add_book(get_db(), user.id, NewBook(title, authors=authors))


def _authors_of(db_app: Flask, book_id: UUID) -> tuple[Author, ...]:
    with db_app.app_context():
        details = find_book_details(get_db(), book_id)
    assert details is not None
    return details.authors


def _entry(db_app: Flask, author: Author) -> AuthorEntry:
    with db_app.app_context():
        page = list_authors(get_db(), f"{author.name} {author.surname}".strip())
    (entry,) = [e for e in page.authors if e.author == author]
    return entry


def _all(db_app: Flask) -> list[tuple[Author, int]]:
    with db_app.app_context():
        page = list_authors(get_db())
    return [(e.author, e.books) for e in page.authors]


def _rename(db_app: Flask, entry: AuthorEntry, author: Author) -> Renamed:
    with db_app.app_context():
        return rename_author(get_db(), entry.id, author)


# Model


def test_list_authors_by_surname_with_their_books(db_app, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Dune Messiah", FRANK)
    _book(db_app, user, "Emma", JANE)
    _book(db_app, user, "Iliad", Author("", "Homer"))

    assert _all(db_app) == [(JANE, 1), (FRANK, 2), (Author("", "Homer"), 1)]


def test_list_authors_searches_and_pages(db_app, user):
    _book(db_app, user, "Dune", FRANK, Author("Brian", "Herbert"))
    _book(db_app, user, "Emma", JANE)

    with db_app.app_context():
        conn = get_db()
        found = list_authors(conn, "herb")
        second = list_authors(conn, "herb", page=9, page_size=1)

    assert [e.author for e in found.authors] == [Author("Brian", "Herbert"), FRANK]
    assert ([e.author for e in second.authors], second.page, second.total) == (
        [FRANK],
        2,
        2,
    )


def test_find_author(db_app, user):
    _book(db_app, user, "Dune", FRANK)
    entry = _entry(db_app, FRANK)

    with db_app.app_context():
        assert find_author(get_db(), entry.id) == entry
        assert find_author(get_db(), uuid4()) is None


def test_rename_an_author_for_every_book(db_app, user):
    dune = _book(db_app, user, "Dune", FRNAK)
    messiah = _book(db_app, user, "Dune Messiah", FRNAK, JANE)

    renamed = _rename(db_app, _entry(db_app, FRNAK), Author(" Frank ", " Herbert "))

    assert renamed == Renamed(FRANK, merged=False)
    assert _authors_of(db_app, dune) == (FRANK,)
    assert _authors_of(db_app, messiah) == (FRANK, JANE)


def test_rename_changes_only_the_case(db_app, user):
    dune = _book(db_app, user, "Dune", Author("frank", "herbert"))

    _rename(db_app, _entry(db_app, Author("frank", "herbert")), FRANK)

    assert _authors_of(db_app, dune) == (FRANK,)


def test_rename_to_an_existing_author_merges_them(db_app, user):
    dune = _book(db_app, user, "Dune", FRNAK)
    messiah = _book(db_app, user, "Dune Messiah", FRANK)

    renamed = _rename(db_app, _entry(db_app, FRNAK), Author("FRANK", "herbert"))

    assert renamed == Renamed(FRANK, merged=True)  # spelled as the existing one
    assert _authors_of(db_app, dune) == (FRANK,)
    assert _authors_of(db_app, messiah) == (FRANK,)
    assert _all(db_app) == [(FRANK, 2)]


def test_merge_keeps_one_author_on_a_book_that_had_both(db_app, user):
    book = _book(db_app, user, "Dune", FRNAK, JANE, FRANK)

    _rename(db_app, _entry(db_app, FRNAK), FRANK)

    assert _authors_of(db_app, book) == (JANE, FRANK)


def test_very_long_search_lists_no_author(db_app, user):
    _book(db_app, user, "Dune", FRANK)

    with db_app.app_context():
        page = list_authors(get_db(), "x" * (SEARCH_MAX_LENGTH + 1))

    assert (page.authors, page.total) == ([], 0)


def test_rename_to_a_name_created_meanwhile_merges(
    db_app, user, other_conn: Connection
):
    dune = _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)
    results: list[Renamed | Exception] = []

    def rename() -> None:
        try:
            results.append(_rename(db_app, entry, FRANK))
        except Exception as e:  # noqa: BLE001 - reported by the assertion below
            results.append(e)

    # Another request creates Frank Herbert, not yet committed...
    with other_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
            (uuid4().bytes, "Frank", "Herbert"),
        )
    # ...while this one renames Frnak to him.
    thread = threading.Thread(target=rename)
    thread.start()
    time.sleep(0.5)
    other_conn.commit()
    thread.join(timeout=30)

    assert results == [Renamed(FRANK, merged=True)]
    assert _authors_of(db_app, dune) == (FRANK,)
    assert _all(db_app) == [(FRANK, 1)]


def test_rename_an_unknown_author(db_app):
    with db_app.app_context(), pytest.raises(UnknownAuthorError):
        rename_author(get_db(), uuid4(), FRANK)


# Routes


def test_authors_page(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Iliad", Author("", "Homer"))

    html = client.get("/authors").get_data(as_text=True)

    assert "<html" in html
    assert re.search(r">Herbert, Frank</span>\s*<span[^>]*>1 book</span>", html)
    assert ">Homer</span>" in html
    assert 'aria-label="Rename Frank Herbert">Rename</a>' in html


def test_authors_search_with_htmx_returns_only_the_list(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Emma", JANE)

    html = client.get("/authors?q=jane", headers=HTMX).get_data(as_text=True)

    assert "<html" not in html
    assert "Austen, Jane" in html
    assert "Herbert" not in html


def test_authors_page_is_in_the_account_menu(client):
    html = client.get("/").get_data(as_text=True)

    assert '<a href="/authors/">Authors</a>' in html


def test_authors_page_needs_login(db_client):
    rv = db_client.get("/authors")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_rename_form_for_the_dialog(db_app, client, user):
    _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)

    html = client.get(f"/authors/{entry.id}", headers=HTMX).get_data(as_text=True)

    assert "<html" not in html
    assert re.search(r'<input [^>]*name="name"[^>]*value="Frnak"', html)
    assert re.search(r'<input [^>]*name="surname"[^>]*value="Herbert"', html)
    assert "1 book" in html


def test_rename_page_without_javascript(db_app, client, user):
    _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)

    html = client.get(f"/authors/{entry.id}").get_data(as_text=True)

    assert "<html" in html
    assert f'action="/authors/{entry.id}"' in html


def test_rename_returns_to_the_list(db_app, client, user, caplog):
    dune = _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)

    with caplog.at_level(logging.INFO):
        rv = client.post(
            f"/authors/{entry.id}",
            data={"name": "Frank", "surname": "Herbert", "q": "herb", "page": "1"},
            headers=HTMX,
        )

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/authors/?q=herb"
    assert _authors_of(db_app, dune) == (FRANK,)
    assert "“Frnak Herbert” renamed to “Frank Herbert”." in client.get(
        "/authors"
    ).get_data(as_text=True)
    assert f"Author 'Frnak Herbert' renamed to 'Frank Herbert' by {user.email}" in (
        caplog.text
    )


def test_rename_to_an_existing_author_says_they_were_merged(db_app, client, user):
    _book(db_app, user, "Dune", FRNAK)
    _book(db_app, user, "Dune Messiah", FRANK)
    entry = _entry(db_app, FRNAK)

    client.post(f"/authors/{entry.id}", data={"name": "Frank", "surname": "Herbert"})

    html = client.get("/authors").get_data(as_text=True)
    assert "“Frnak Herbert” merged into “Frank Herbert”." in html


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"name": "Frank", "surname": ""}, "This field is required."),
        ({"name": "x" * 128, "surname": "Herbert"}, "127 characters"),
    ],
)
def test_rename_with_errors_shows_the_form_again(db_app, client, user, data, error):
    dune = _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)

    rv = client.post(f"/authors/{entry.id}", data=data, headers=HTMX)

    assert rv.status_code == 200
    assert error in rv.get_data(as_text=True)
    assert _authors_of(db_app, dune) == (FRNAK,)


@pytest.mark.parametrize("method", ["get", "post"])
def test_unknown_author_is_not_found(client, method):
    rv = getattr(client, method)(f"/authors/{uuid4()}", data={"surname": "X"})

    assert rv.status_code == 404


def test_author_merged_away_meanwhile_is_not_found(
    db_app, client, user, monkeypatch: pytest.MonkeyPatch
):
    """The form was opened, then the author was merged into another one."""
    gone = AuthorEntry(uuid4(), FRNAK, 1)
    monkeypatch.setattr("bookshelf.routes.authors.find_author", lambda *_args: gone)

    rv = client.post(f"/authors/{gone.id}", data={"name": "A", "surname": "B"})

    assert rv.status_code == 404
