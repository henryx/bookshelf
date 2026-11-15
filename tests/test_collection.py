"""The current user's collection: listing it, and adding, changing and removing books."""

import re
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import (
    Author,
    Book,
    NewBook,
    Sort,
    UnknownBookError,
    add_book,
    add_to_collection,
    find_book,
    find_book_by_isbn,
    list_books,
    list_collection,
    remove_from_collection,
    set_copies,
    set_read,
)
from bookshelf.models.users import User, create_user

HTMX = {"HX-Request": "true"}
FROM_LIST = {**HTMX, "HX-Target": "book-list"}


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


@pytest.fixture
def other(db_app: Flask, password: str) -> User:
    """Another user, with a collection of their own."""
    with db_app.app_context():
        return create_user(get_db(), "other@example.com", password)


def _book(
    app: Flask,
    owner: User,
    title: str,
    *,
    copies: int = 1,
    isbn: str | None = None,
    authors: tuple[Author, ...] = (),
) -> UUID:
    """A catalogue book, in `owner`'s collection."""
    with app.app_context():
        book = NewBook(title, authors, isbn=isbn)
        return add_book(get_db(), owner.id, book, copies=copies)


def _copies(app: Flask, user: User, book_id: UUID) -> int | None:
    with app.app_context(), get_db().cursor() as cur:
        cur.execute(
            "SELECT COPIES FROM COLLECTION_BOOKS WHERE USER_ID = ? AND BOOK_ID = ?",
            (user.id.bytes, book_id.bytes),
        )
        row = cur.fetchone()
        return row[0] if row else None


def _in_catalogue(app: Flask, book_id: UUID) -> bool:
    with app.app_context(), get_db().cursor() as cur:
        cur.execute("SELECT 1 FROM BOOKS WHERE ID = ?", (book_id.bytes,))
        return cur.fetchone() is not None


def _titles(html: str) -> list[str]:
    return re.findall(r'<a class="book-title"[^>]*>([^<]*)</a>', html)


# Model


def test_list_books_gives_the_users_copies(db_app, user, other):
    _book(db_app, user, "Dune", copies=2)
    _book(db_app, other, "Emma", copies=3)

    with db_app.app_context():
        books = list_books(get_db(), user.id).books

    assert [(b.title, b.copies) for b in books] == [("Dune", 2), ("Emma", None)]


def test_list_collection_has_only_the_users_books(db_app, user, other):
    _book(db_app, user, "Dune", copies=2)
    _book(db_app, other, "Emma")

    with db_app.app_context():
        page = list_collection(get_db(), user.id)

    assert [(b.title, b.copies) for b in page.books] == [("Dune", 2)]
    assert page.total == 1


def test_list_collection_searches_sorts_and_pages(db_app, user):
    _book(db_app, user, "Dune", authors=(Author("Frank", "Herbert"),))
    _book(db_app, user, "Emma", authors=(Author("Jane", "Austen"),))
    _book(db_app, user, "Persuasion", authors=(Author("Jane", "Austen"),))

    with db_app.app_context():
        conn = get_db()
        found = list_collection(conn, user.id, "jane")
        by_author = list_collection(conn, user.id, sort=Sort.AUTHOR)
        second = list_collection(conn, user.id, page=2, page_size=2)

    assert [b.title for b in found.books] == ["Emma", "Persuasion"]
    assert [b.title for b in by_author.books] == ["Emma", "Persuasion", "Dune"]
    assert by_author.books[2].authors == (Author("Frank", "Herbert"),)
    assert ([b.title for b in second.books], second.page) == (["Persuasion"], 2)
    assert second.total == 3


def test_list_collection_out_of_range_gives_the_last_page(db_app, user, other):
    for title in ("Dune", "Emma", "Persuasion"):
        _book(db_app, user, title)
    _book(db_app, other, "Ulysses")

    with db_app.app_context():
        conn = get_db()
        last = list_collection(conn, user.id, page=9, page_size=2)
        empty = list_collection(conn, user.id, "nothing like this", page=3)

    assert ([b.title for b in last.books], last.page, last.total) == (
        ["Persuasion"],
        2,
        3,
    )
    assert (empty.books, empty.page, empty.total) == ([], 1, 0)


def test_add_to_collection(db_app, user, other):
    book_id = _book(db_app, other, "Emma")

    with db_app.app_context():
        added = add_to_collection(get_db(), user.id, book_id, copies=2)

    assert added
    assert _copies(db_app, user, book_id) == 2
    assert _copies(db_app, other, book_id) == 1


def test_add_to_collection_twice_keeps_the_copies(db_app, user):
    book_id = _book(db_app, user, "Dune", copies=3)

    with db_app.app_context():
        added = add_to_collection(get_db(), user.id, book_id, copies=1)

    assert not added
    assert _copies(db_app, user, book_id) == 3


def test_set_copies(db_app, user):
    book_id = _book(db_app, user, "Dune")

    with db_app.app_context():
        assert set_copies(get_db(), user.id, book_id, 4)

    assert _copies(db_app, user, book_id) == 4


def test_set_copies_of_a_book_not_in_the_collection(db_app, user, other):
    book_id = _book(db_app, other, "Emma")

    with db_app.app_context():
        assert not set_copies(get_db(), user.id, book_id, 4)

    assert _copies(db_app, user, book_id) is None


def test_remove_from_collection_keeps_the_book_in_the_catalogue(db_app, user):
    book_id = _book(db_app, user, "Dune")

    with db_app.app_context():
        assert remove_from_collection(get_db(), user.id, book_id)
        assert not remove_from_collection(get_db(), user.id, book_id)

    assert _copies(db_app, user, book_id) is None
    assert _in_catalogue(db_app, book_id)


def test_find_book(db_app, user, other):
    authors = (Author("Terry", "Pratchett"), Author("Neil", "Gaiman"))
    book_id = _book(db_app, user, "Good Omens", copies=2, authors=authors)

    with db_app.app_context():
        conn = get_db()
        mine = find_book(conn, user.id, book_id)
        theirs = find_book(conn, other.id, book_id)
        unknown = find_book(conn, user.id, uuid4())

    assert mine is not None and theirs is not None
    assert (mine.title, mine.authors, mine.copies) == ("Good Omens", authors, 2)
    assert theirs.copies is None
    assert unknown is None


def test_find_book_by_isbn(db_app, user):
    book_id = _book(db_app, user, "Dune", isbn="9780441172719")

    with db_app.app_context():
        found = find_book_by_isbn(get_db(), user.id, "9780441172719")
        missing = find_book_by_isbn(get_db(), user.id, "9780140449136")

    assert found is not None and (found.id, found.copies) == (book_id, 1)
    assert missing is None


# The collection page


def test_collection_page_requires_login(db_client):
    rv = db_client.get("/")

    assert rv.status_code == 302
    assert "/login" in rv.headers["Location"]


def test_collection_page_lists_own_books_with_copies(db_app, client, user, other):
    book_id = _book(db_app, user, "Dune", copies=2)
    _book(db_app, other, "Emma")

    html = client.get("/").get_data(as_text=True)

    assert "<html" in html
    assert "Your collection" in html
    assert _titles(html) == ["Dune"]
    assert '<span class="book-copies">Copies · 2</span>' in html
    assert "copies-form" not in html
    assert f'action="/collection/books/{book_id}/remove"' in html
    assert "hx-indicator" in html


def test_empty_collection_says_so(client):
    html = client.get("/").get_data(as_text=True)

    assert "Your collection is empty" in html


def test_collection_search_with_htmx_returns_only_the_list(db_app, client, user):
    _book(db_app, user, "Dune")
    _book(db_app, user, "Emma")

    rv = client.get("/?q=emm", headers=HTMX)
    html = rv.get_data(as_text=True)

    assert "<html" not in html
    assert _titles(html) == ["Emma"]


def test_account_menu_links_to_the_collection(client):
    html = client.get("/catalogue").get_data(as_text=True)

    assert 'href="/"' in html


# The catalogue's rows


def test_catalogue_rows_show_whether_the_book_is_in_the_collection(
    db_app, client, user, other
):
    _book(db_app, user, "Dune", copies=2)
    emma = _book(db_app, other, "Emma")

    html = client.get("/catalogue").get_data(as_text=True)

    assert "In your collection · 2" in html
    assert html.count('action="/collection/books/') == 1
    assert f'action="/collection/books/{emma}/add"' in html


# Adding


def test_add_to_collection_refuses_a_book_not_in_the_catalogue(db_app, user):
    with db_app.app_context(), pytest.raises(UnknownBookError):
        add_to_collection(get_db(), user.id, uuid4())


def test_add_a_book_deleted_meanwhile_is_not_found(
    db_app, client, monkeypatch: pytest.MonkeyPatch
):
    """The book is found, then deleted by someone else before it is added."""
    gone = Book(uuid4(), "Gone", (), None)
    monkeypatch.setattr("bookshelf.routes.collection.find_book", lambda *_args: gone)

    rv = client.post(f"/collection/books/{gone.id}/add", data={"copies": "1"})

    assert rv.status_code == 404


def test_add_from_the_list_returns_the_list_with_a_notice(db_app, client, user, other):
    emma = _book(db_app, other, "Emma")
    _book(db_app, other, "Dune")

    rv = client.post(
        f"/collection/books/{emma}/add",
        data={"copies": "1", "q": "emm", "sort": "title", "page": "1"},
        headers=FROM_LIST,
    )
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert "“Emma” added to your collection." in html
    assert _titles(html) == ["Emma"]  # the search is kept
    assert "In your collection · 1" in html
    assert _copies(db_app, user, emma) == 1


def test_add_without_htmx_redirects_to_the_catalogue(db_app, client, user, other):
    emma = _book(db_app, other, "Emma")

    rv = client.post(
        f"/collection/books/{emma}/add",
        data={"copies": "1", "q": "emm", "sort": "author", "page": "1"},
    )

    assert rv.status_code == 302
    location = urlsplit(rv.headers["Location"])
    assert location.path == "/catalogue"
    assert parse_qs(location.query) == {"q": ["emm"], "sort": ["author"]}
    html = client.get(rv.headers["Location"]).get_data(as_text=True)
    assert "“Emma” added to your collection." in html


def test_add_from_the_add_book_dialog_redirects(db_app, client, user, other):
    emma = _book(db_app, other, "Emma")

    rv = client.post(
        f"/collection/books/{emma}/add",
        data={"title": "Emma", "copies": "3"},
        headers={**HTMX, "HX-Target": "add-book-form"},
    )

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/catalogue"
    assert _copies(db_app, user, emma) == 3


def test_add_a_book_already_in_the_collection(db_app, client, user):
    dune = _book(db_app, user, "Dune", copies=2)

    rv = client.post(
        f"/collection/books/{dune}/add", data={"copies": "1"}, headers=FROM_LIST
    )

    assert "“Dune” is already in your collection." in rv.get_data(as_text=True)
    assert _copies(db_app, user, dune) == 2


def test_add_an_unknown_book(client):
    rv = client.post(
        f"/collection/books/{uuid4()}/add", data={"copies": "1"}, headers=FROM_LIST
    )

    assert rv.status_code == 404


@pytest.mark.parametrize("copies", ["0", "1000", "many"])
def test_add_with_invalid_copies(db_app, client, user, other, copies):
    emma = _book(db_app, other, "Emma")

    rv = client.post(
        f"/collection/books/{emma}/add", data={"copies": copies}, headers=FROM_LIST
    )

    assert rv.status_code == 400
    assert _copies(db_app, user, emma) is None


def test_collection_changes_require_login(db_app, db_client, user):
    dune = _book(db_app, user, "Dune")

    for action in ("add", "remove"):
        rv = db_client.post(f"/collection/books/{dune}/{action}", data={"copies": "1"})
        assert rv.status_code == 302
        assert "/login" in rv.headers["Location"]


def test_remove_a_book_not_in_the_collection_shows_an_error(
    db_app, client, user, other
):
    emma = _book(db_app, other, "Emma")

    rv = client.post(f"/collection/books/{emma}/remove", headers=FROM_LIST)
    html = rv.get_data(as_text=True)

    assert re.search(r'class="error"[^>]*>“Emma” is not in your collection.', html)


# Removing


def test_remove_from_the_list(db_app, client, user):
    dune = _book(db_app, user, "Dune")
    _book(db_app, user, "Emma")

    rv = client.post(f"/collection/books/{dune}/remove", headers=FROM_LIST)
    html = rv.get_data(as_text=True)

    assert "“Dune” removed from your collection." in html
    assert _titles(html) == ["Emma"]
    assert _copies(db_app, user, dune) is None
    assert _in_catalogue(db_app, dune)


def test_remove_without_htmx_redirects_to_the_collection(db_app, client, user):
    dune = _book(db_app, user, "Dune")

    rv = client.post(f"/collection/books/{dune}/remove", data={"q": "du"})

    assert rv.status_code == 302
    location = urlsplit(rv.headers["Location"])
    assert (location.path, parse_qs(location.query)) == ("/", {"q": ["du"]})
    html = client.get(rv.headers["Location"]).get_data(as_text=True)
    assert "“Dune” removed from your collection." in html


# A duplicate ISBN in the add-book form


def test_duplicate_isbn_offers_to_add_the_existing_book(db_app, client, other):
    emma = _book(db_app, other, "Emma", isbn="9780141439587")

    rv = client.post(
        "/books/new",
        data={"title": "Emma", "isbn": "978-0-14-143958-7", "copies": "1"},
        headers=HTMX,
    )
    html = rv.get_data(as_text=True)

    assert "already in the catalogue" in html
    assert f'formaction="/collection/books/{emma}/add"' in html
    assert f'hx-post="/collection/books/{emma}/add"' in html


def test_duplicate_isbn_offer_from_the_collection_keeps_the_list(db_app, client, other):
    emma = _book(db_app, other, "Emma", isbn="9780141439587")
    form = {"title": "Emma", "isbn": "9780141439587", "copies": "1"}

    offer = client.post(
        "/books/new", data={**form, "list": "collection"}, headers=HTMX
    ).get_data(as_text=True)
    rv = client.post(
        f"/collection/books/{emma}/add",
        data={**form, "list": "collection"},
        headers=HTMX,
    )

    assert '<input type="hidden" name="list" value="collection">' in offer
    assert rv.headers["HX-Redirect"] == "/"


def test_duplicate_isbn_of_a_book_in_the_collection_says_so(db_app, client, user):
    dune = _book(db_app, user, "Dune", isbn="9780441172719", copies=2)

    rv = client.post(
        "/books/new",
        data={"title": "Dune", "isbn": "9780441172719", "copies": "1"},
        headers=HTMX,
    )
    html = rv.get_data(as_text=True)

    assert "“Dune” is already in your collection" in html
    assert f"/collection/books/{dune}/add" not in html


# Read


def _read(db_app: Flask, user: User) -> dict[str, bool]:
    with db_app.app_context():
        return {b.title: b.read for b in list_collection(get_db(), user.id).books}


def test_books_start_as_not_read(db_app, user, other):
    _book(db_app, user, "Dune")
    emma = _book(db_app, other, "Emma")
    with db_app.app_context():
        add_to_collection(get_db(), user.id, emma)

    assert _read(db_app, user) == {"Dune": False, "Emma": False}


def test_set_read_is_per_user(db_app, user, other):
    dune = _book(db_app, user, "Dune")
    with db_app.app_context():
        add_to_collection(get_db(), other.id, dune)
        assert set_read(get_db(), user.id, dune, read=True)

    assert _read(db_app, user) == {"Dune": True}
    assert _read(db_app, other) == {"Dune": False}

    with db_app.app_context():
        assert set_read(get_db(), user.id, dune, read=False)
    assert _read(db_app, user) == {"Dune": False}


def test_set_read_of_a_book_not_in_the_collection(db_app, user, other):
    emma = _book(db_app, other, "Emma")

    with db_app.app_context():
        assert not set_read(get_db(), user.id, emma, read=True)


def test_collection_rows_have_a_read_checkbox(db_app, client, user):
    _book(db_app, user, "Dune")

    html = client.get("/").get_data(as_text=True)

    checkbox = re.search(r'<input type="checkbox" name="read"[^>]*>', html)
    assert checkbox is not None
    assert 'aria-label="Dune read"' in checkbox[0]
    assert "checked" not in checkbox[0]
    # Saved as soon as it changes; a Save button only without JavaScript.
    assert re.search(r'<form [^>]*hx-trigger="change"[^>]*>', html)
    assert re.search(r"<noscript>\s*<button[^>]*>Save</button>\s*</noscript>", html)


def test_the_catalogue_has_no_read_checkbox(db_app, client, user):
    _book(db_app, user, "Dune")

    assert 'name="read"' not in client.get("/catalogue").get_data(as_text=True)


def test_mark_a_book_read_from_the_list(db_app, client, user):
    dune = _book(db_app, user, "Dune")

    rv = client.post(
        f"/collection/books/{dune}/read",
        data={"read": "y", "page": "1"},
        headers=FROM_LIST,
    )
    html = rv.get_data(as_text=True)

    assert "“Dune” marked as read." in html
    assert re.search(r'<input type="checkbox" name="read"[^>]*checked', html)
    assert _read(db_app, user) == {"Dune": True}


def test_unmark_a_book_without_javascript(db_app, client, user):
    dune = _book(db_app, user, "Dune")
    with db_app.app_context():
        set_read(get_db(), user.id, dune, read=True)

    rv = client.post(f"/collection/books/{dune}/read", data={"q": "du"})

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/?q=du"
    assert _read(db_app, user) == {"Dune": False}
    assert "“Dune” marked as not read." in client.get("/").get_data(as_text=True)


def test_mark_read_a_book_not_in_the_collection_says_so(db_app, client, user, other):
    emma = _book(db_app, other, "Emma")

    rv = client.post(
        f"/collection/books/{emma}/read", data={"read": "y"}, headers=FROM_LIST
    )

    assert re.search(
        r'class="error"[^>]*>“Emma” is not in your collection.',
        rv.get_data(as_text=True),
    )


def test_mark_read_an_unknown_book_is_not_found(client):
    assert client.post(f"/collection/books/{uuid4()}/read").status_code == 404


def test_top_bar_leads_to_the_collection_and_the_catalogue(client):
    html = client.get("/catalogue").get_data(as_text=True)

    assert '<h1><a href="/">Bookshelf</a></h1>' in html
    assert '<a href="/catalogue">Catalogue</a>' in html
