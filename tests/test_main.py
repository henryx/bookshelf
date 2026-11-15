"""The main page: the book list with search, sorting and pages."""

import re
from datetime import date
from uuid import uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import PAGE_SIZE, Sort, list_books
from bookshelf.models.users import User

HTMX = {"HX-Request": "true"}


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


def _add_book(
    app: Flask,
    title: str,
    authors: tuple[str, ...] = (),
    *,
    isbn: str | None = None,
    year: int | None = None,
    added: date | None = None,
) -> None:
    with app.app_context():
        conn = get_db()
        book_id = uuid4().bytes
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO BOOKS (ID, TITLE, ISBN, PUBLICATION_YEAR, CREATED_AT) "
                "VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
                (book_id, title, isbn, year, added),
            )
            for order, full_name in enumerate(authors, start=1):
                # "Terry Pratchett": name "Terry", surname "Pratchett".
                name, _, surname = full_name.rpartition(" ")
                cur.execute(
                    "SELECT ID FROM AUTHORS WHERE NAME = ? AND SURNAME = ?",
                    (name, surname),
                )
                row = cur.fetchone()
                author_id = row[0] if row else uuid4().bytes
                if row is None:
                    cur.execute(
                        "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
                        (author_id, name, surname),
                    )
                cur.execute(
                    "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) "
                    "VALUES (?, ?, ?)",
                    (book_id, author_id, order),
                )
        conn.commit()


def _titles(html: str) -> list[str]:
    return re.findall(r'<a class="book-title"[^>]*>([^<]*)</a>', html)


def test_main_page_lists_books_with_authors_and_year(db_app, client):
    _add_book(db_app, "Good Omens", ("Terry Pratchett", "Neil Gaiman"), year=1990)

    html = client.get("/").get_data(as_text=True)

    assert "<html" in html
    assert _titles(html) == ["Good Omens"]
    assert "Terry Pratchett, Neil Gaiman" in html
    assert "1990" in html
    assert 'name="q"' in html
    assert "hx-indicator" in html


def test_main_page_without_books_says_so(client):
    html = client.get("/").get_data(as_text=True)

    assert _titles(html) == []
    assert "No books in the library yet." in html


def test_books_sorted_by_title_ignoring_case_by_default(db_app, client):
    for title in ("dune", "Emma", "Anathem"):
        _add_book(db_app, title)

    html = client.get("/").get_data(as_text=True)

    assert _titles(html) == ["Anathem", "dune", "Emma"]


def test_books_sorted_by_first_author_without_authors_last(db_app, client):
    """By surname, then name, ignoring case."""
    _add_book(db_app, "A", ("Zadie Smith",))
    _add_book(db_app, "B")
    _add_book(db_app, "C", ("ann Leckie", "Zadie Smith"))
    _add_book(db_app, "D", ("Banks",))
    _add_book(db_app, "E", ("Adam Smith",))

    html = client.get("/?sort=author").get_data(as_text=True)

    assert _titles(html) == ["D", "C", "E", "A", "B"]


def test_books_sorted_by_date_added_newest_first(db_app, client):
    _add_book(db_app, "Old", added=date(2020, 1, 1))
    _add_book(db_app, "New", added=date(2024, 1, 1))
    _add_book(db_app, "Middle", added=date(2022, 1, 1))

    html = client.get("/?sort=added").get_data(as_text=True)

    assert _titles(html) == ["New", "Middle", "Old"]


def test_unknown_sort_falls_back_to_title(db_app, client):
    _add_book(db_app, "B")
    _add_book(db_app, "A")

    rv = client.get("/?sort=nonsense")

    assert rv.status_code == 200
    assert _titles(rv.get_data(as_text=True)) == ["A", "B"]


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("dune", ["Dune"]),  # title, ignoring case
        ("HERBERT", ["Dune"]),  # author's surname, ignoring case
        ("frank", ["Dune"]),  # author's name
        ("Frank Herbert", ["Dune"]),  # author's name and surname
        ("leckie", ["Ancillary Justice"]),  # second author
        ("978-0-441", ["Dune"]),  # ISBN, hyphens ignored
        ("0441172717", ["Dune"]),  # ISBN-10 of the stored ISBN-13
        ("9780441172", ["Dune"]),  # first 10 digits of the ISBN-13, as typed
        ("  Justice ", ["Ancillary Justice"]),  # spaces around ignored
        ("nothing like this", []),
    ],
)
def test_search_matches_title_author_or_isbn(db_app, client, search, expected):
    _add_book(db_app, "Dune", ("Frank Herbert",), isbn="9780441172719")
    _add_book(db_app, "Ancillary Justice", ("Someone", "Ann Leckie"))

    html = client.get("/", query_string={"q": search}).get_data(as_text=True)

    assert _titles(html) == expected


def test_search_by_isbn_13_start_that_is_also_a_valid_isbn_10(db_app, client):
    # 9780441174 is a valid ISBN-10 too: the book is still found as typed.
    _add_book(db_app, "Dune Messiah", isbn="9780441174706")

    html = client.get("/", query_string={"q": "9780441174"}).get_data(as_text=True)

    assert _titles(html) == ["Dune Messiah"]


def test_search_without_matches_says_so(db_app, client):
    _add_book(db_app, "Dune")

    html = client.get("/?q=zzz").get_data(as_text=True)

    assert "No books match" in html
    assert "zzz" in html


def test_very_long_search_is_not_an_error(db_app, client):
    _add_book(db_app, "Dune")

    rv = client.get("/", query_string={"q": "x" * 1000})

    assert rv.status_code == 200
    assert _titles(rv.get_data(as_text=True)) == []


def test_books_shown_in_pages(db_app, client):
    for n in range(PAGE_SIZE + 2):
        _add_book(db_app, f"Book {n:03}")

    first = client.get("/").get_data(as_text=True)
    second = client.get("/?page=2").get_data(as_text=True)

    assert len(_titles(first)) == PAGE_SIZE
    assert _titles(second) == ["Book 050", "Book 051"]
    assert "Page 1 of 2" in first
    assert "page=2" in first
    assert f"{PAGE_SIZE + 2} books" in first


def test_page_links_keep_search_and_sort(db_app, client):
    for n in range(PAGE_SIZE + 1):
        _add_book(db_app, f"Book {n:03}")

    html = client.get("/?q=book&sort=added").get_data(as_text=True)

    links = re.findall(r'href="(/\?[^"]*page=2[^"]*)"', html)
    assert links
    assert "q=book" in links[0]
    assert "sort=added" in links[0]


@pytest.mark.parametrize("page", ["0", "-3", "abc", "99"])
def test_page_out_of_range_shows_nearest_page(db_app, client, page):
    _add_book(db_app, "Dune")

    rv = client.get(f"/?page={page}")

    assert rv.status_code == 200
    assert _titles(rv.get_data(as_text=True)) == ["Dune"]


def test_htmx_request_returns_only_the_list(db_app, client):
    _add_book(db_app, "Dune")

    html = client.get("/?q=dune", headers=HTMX).get_data(as_text=True)

    assert "<html" not in html
    assert 'id="book-search-q"' not in html  # the search box
    assert _titles(html) == ["Dune"]


def test_htmx_history_restore_returns_full_page(db_app, client):
    rv = client.get("/?q=dune", headers={**HTMX, "HX-History-Restore-Request": "true"})

    assert "<html" in rv.get_data(as_text=True)


def test_list_books_returns_total_and_pages(db_app, user):
    for n in range(3):
        _add_book(db_app, f"Book {n}")

    with db_app.app_context():
        page = list_books(get_db(), user.id, "", Sort.TITLE, 2, page_size=2)

    assert [book.title for book in page.books] == ["Book 2"]
    assert page.total == 3
    assert page.page == 2
    assert page.pages == 2


def test_list_books_out_of_range_gives_the_last_page_with_its_total(db_app, user):
    for n in range(3):
        _add_book(db_app, f"Book {n}")

    with db_app.app_context():
        page = list_books(get_db(), user.id, "book", Sort.ADDED, 7, page_size=2)

    assert (len(page.books), page.total, page.page) == (1, 3, 2)
