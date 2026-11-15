"""Adding a book from the main page."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from firebird.driver import connect
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
        volume=" II ",
        tags="fantasy, Humour,  , FANTASY",
        notes="Signed copy",
        copies="2",
    )

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/"
    row = _rows(
        db_app,
        "SELECT ID, TITLE, ISBN, PUBLISHER, PUBLICATION_YEAR, VOLUME, NOTES FROM BOOKS",
    )
    assert len(row) == 1
    book_id, *details = row[0]
    assert details == [
        "Good Omens",
        "9780552137034",
        "Gollancz",
        1990,
        "II",
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
    assert re.search(r'<a class="book-title"[^>]*>Dune</a>', html)


def test_add_book_with_title_only_leaves_the_rest_empty(db_app, client):
    _post(client, title="Untitled notes")

    row = _rows(
        db_app,
        "SELECT ISBN, PUBLISHER, PUBLICATION_YEAR, VOLUME, NOTES FROM BOOKS",
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


@pytest.mark.parametrize(
    "rows",
    [
        (("Ann", "Leckie"), ("Ann", "Leckie")),
        (("Ann", "Leckie"), ("Neil", "Gaiman"), (" ann ", "LECKIE ")),
        (("", "Homer"), ("", "homer")),
    ],
)
def test_add_book_refuses_the_same_author_twice(db_app, client, rows):
    rv = _post(client, **_authors(*rows))
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert html.count("This author is already listed.") == 1
    assert "Each author can be listed only once." in html
    assert _scalar(db_app, "SELECT COUNT(*) FROM BOOKS") == 0


def test_add_book_marks_only_the_repeated_row(client):
    html = _post(
        client, **_authors(("Ann", "Leckie"), ("Neil", "Gaiman"), ("Ann", "Leckie"))
    ).get_data(as_text=True)

    rows = re.findall(r'<div class="author-row".*?</div>', html, re.DOTALL)
    marked = ["already listed" in row for row in rows if "authors-__index__" not in row]
    assert marked == [False, False, True]


def test_add_book_allows_authors_sharing_a_surname(db_app, client):
    _post(client, **_authors(("Charlotte", "Brontë"), ("Emily", "Brontë")))

    assert _scalar(db_app, "SELECT COUNT(*) FROM BOOK_AUTHORS") == 2


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
        ({"volume": "x" * 33}, "Field cannot be longer than 32 characters."),
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
        books = list_books(conn, user.id).books

    assert [(b.id, b.title, b.authors) for b in books] == [
        (book_id, "Dune", (Author("Frank", "Herbert"),))
    ]


def test_add_book_uses_author_added_concurrently(db_app, user):
    """
    An author committed by someone else after this transaction started is not
    visible to it, so its insert clashes: the book gets that author instead.
    """
    cfg = db_app.config
    with db_app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            # Start this request's transaction, as loading the user does.
            cur.execute("SELECT COUNT(*) FROM AUTHORS")
            cur.fetchone()
        with (
            connect(
                cfg["DATABASE_DSN"],
                user=cfg["DATABASE_USER"],
                password=cfg["DATABASE_PASSWORD"],
                charset="UTF8",
            ) as other,
            other.cursor() as cur,
        ):
            cur.execute(
                "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
                (uuid4().bytes, "Frank", "Herbert"),
            )
            other.commit()

        add_book(
            conn, user.id, NewBook(title="Dune", authors=(Author("FRANK", "HERBERT"),))
        )
        # The book was added in its own transaction: end the old snapshot.
        conn.rollback()
        books = list_books(conn, user.id).books

    assert [(b.title, b.authors) for b in books] == [
        ("Dune", (Author("Frank", "Herbert"),))
    ]
    assert _scalar(db_app, "SELECT COUNT(*) FROM AUTHORS") == 1


def _wait_until_author_insert_blocks(app: Flask) -> None:
    """
    Wait until another attachment's author insert is running, i.e. waiting for
    the lock on the uncommitted author. Each check is a new transaction, so it
    sees fresh monitoring data.
    """
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        running = _scalar(
            app,
            "SELECT COUNT(*) FROM MON$STATEMENTS "
            "WHERE MON$STATE = 1 AND MON$ATTACHMENT_ID <> CURRENT_CONNECTION "
            "AND MON$SQL_TEXT CONTAINING 'INSERT INTO AUTHORS'",
        )
        if running:
            return
        time.sleep(0.05)
    pytest.fail("The author insert never started waiting")


def test_add_book_uses_author_committed_while_inserting(db_app, user, other_conn):
    """
    Someone else's new author, still uncommitted when the book looks it up,
    is committed while the book's insert of the same author waits: the insert
    clashes, and the book gets their author.
    """
    theirs = uuid4().bytes
    with other_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
            (theirs, "Frank", "Herbert"),
        )

    def add() -> None:
        with db_app.app_context():
            add_book(
                get_db(),
                user.id,
                NewBook(title="Dune", authors=(Author("FRANK", "HERBERT"),)),
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        added = executor.submit(add)
        try:
            _wait_until_author_insert_blocks(db_app)
        finally:
            # Always release the lock, or the executor would wait forever.
            other_conn.commit()
        added.result(timeout=10)

    assert _rows(db_app, "SELECT AUTHOR_ID FROM BOOK_AUTHORS") == [(theirs,)]
    assert _scalar(db_app, "SELECT COUNT(*) FROM AUTHORS") == 1


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


# Author suggestions


def _suggest(client: FlaskClient, field: str, q: str):
    return client.get(
        "/authors/suggestions", query_string={"field": field, "q": q}, headers=HTMX
    )


def _options(html: str) -> list[tuple[str, str, str]]:
    """(value, data-name, data-surname) of each <option>."""
    return re.findall(
        r'<option value="([^"]*)" data-name="([^"]*)" data-surname="([^"]*)"', html
    )


@pytest.fixture
def known_authors(client):
    _post(client, title="Mort", **_authors(("Terry", "Pratchett")))
    _post(client, title="Brooklyn", **_authors(("Colm", "Tóibín")))
    _post(client, title="Iliad", **_authors(("", "Homer")))
    _post(client, title="Thud", **_authors(("Terence", "Hanbury White")))


def test_suggestions_by_name_start_ignoring_case(client, known_authors):
    html = _suggest(client, "name", "ter").get_data(as_text=True)

    assert _options(html) == [
        ("Terence Hanbury White", "Terence", "Hanbury White"),
        ("Terry Pratchett", "Terry", "Pratchett"),
    ]


def test_suggestions_by_surname_start_ignoring_case(client, known_authors):
    html = _suggest(client, "surname", "HO").get_data(as_text=True)

    assert _options(html) == [("Homer", "", "Homer")]


def test_suggestions_do_not_match_inside_names(client, known_authors):
    html = _suggest(client, "surname", "chett").get_data(as_text=True)

    assert _options(html) == []


@pytest.mark.parametrize("q", ["", "   ", "x" * 128])
def test_no_suggestions_for_blank_or_too_long_text(client, known_authors, q):
    rv = _suggest(client, "name", q)

    assert rv.status_code == 200
    assert _options(rv.get_data(as_text=True)) == []


def test_suggestions_are_limited(client):
    for n in range(12):
        _post(client, title=f"Book {n}", **_authors((f"Ann {n:02}", "Smith")))

    html = _suggest(client, "surname", "smith").get_data(as_text=True)

    assert len(_options(html)) == 10


def test_suggestions_reject_unknown_field(client):
    assert _suggest(client, "title", "dune").status_code == 400


def test_suggestions_require_login(db_client):
    rv = db_client.get("/authors/suggestions?field=name&q=t")

    assert rv.status_code == 302


def test_author_inputs_ask_for_suggestions(client):
    html = client.get("/books/new").get_data(as_text=True)

    for field in ("name", "surname"):
        input_tag = re.search(rf'<input id="book-authors-0-{field}"[^>]*>', html)
        assert input_tag
        assert f'list="book-authors-0-{field}-suggestions"' in input_tag[0]
        assert 'hx-get="/authors/suggestions"' in input_tag[0]
        # Not the form's outerHTML (inherited): that would replace the <datalist>.
        assert 'hx-swap="innerHTML"' in input_tag[0]
        assert f'<datalist id="book-authors-0-{field}-suggestions">' in html
    assert '<datalist id="book-authors-__index__-name-suggestions">' in html


# Publisher suggestions


def _suggest_publisher(client: FlaskClient, q: str):
    return client.get("/publishers/suggestions", query_string={"q": q}, headers=HTMX)


def _values(html: str) -> list[str]:
    return re.findall(r'<option value="([^"]*)"', html)


def test_publisher_suggestions_by_start_ignoring_case(client):
    _post(client, title="Mort", publisher="Gollancz")
    _post(client, title="Thud", publisher="GOLLANCZ")
    _post(client, title="Dune", publisher="Chilton Books")
    _post(client, title="Emma", publisher="Gollancz Classics")

    html = _suggest_publisher(client, "gol").get_data(as_text=True)

    assert len(_values(html)) == 2
    assert _values(html)[0].casefold() == "gollancz"
    assert _values(html)[1] == "Gollancz Classics"


def test_publisher_suggestions_do_not_match_inside_names(client):
    _post(client, title="Dune", publisher="Chilton Books")

    assert _values(_suggest_publisher(client, "books").get_data(as_text=True)) == []


@pytest.mark.parametrize("q", ["", "   ", "x" * 256])
def test_no_publisher_suggestions_for_blank_or_too_long_text(client, q):
    _post(client, title="Mort", publisher="Gollancz")

    rv = _suggest_publisher(client, q)

    assert rv.status_code == 200
    assert _values(rv.get_data(as_text=True)) == []


def test_publisher_suggestions_are_limited(client):
    for n in range(12):
        _post(client, title=f"Book {n}", publisher=f"Press {n:02}")

    assert (
        len(_values(_suggest_publisher(client, "press").get_data(as_text=True))) == 10
    )


def test_publisher_suggestions_require_login(db_client):
    assert db_client.get("/publishers/suggestions?q=g").status_code == 302


def test_publisher_input_asks_for_suggestions(client):
    html = client.get("/books/new").get_data(as_text=True)

    input_tag = re.search(r'<input [^>]*id="book-publisher"[^>]*>', html)
    assert input_tag
    assert 'list="book-publisher-suggestions"' in input_tag[0]
    assert 'hx-get="/publishers/suggestions"' in input_tag[0]
    assert 'hx-swap="innerHTML"' in input_tag[0]
    assert '<datalist id="book-publisher-suggestions">' in html
