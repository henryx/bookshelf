"""The authors page: listing authors, and renaming one for every book (merging
it into another author with that name)."""

import logging
import re
import threading
import time
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient
from raw_db import RawConnection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.db import get_session
from bookshelf.models.authors import (
    AuthorEntry,
    Renamed,
    UnknownAuthorError,
    find_author,
    list_authors,
    rename_author,
)
from bookshelf.models.authors import renaming as authors_model
from bookshelf.models.books import (
    SEARCH_MAX_LENGTH,
    Author,
    NewBook,
    Sort,
    add_book,
    find_book_details,
    list_books,
    list_collection,
)
from bookshelf.models.users import User, create_user

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
        return add_book(get_session(), user.id, NewBook(title, authors=authors))


def _authors_of(db_app: Flask, book_id: UUID) -> tuple[Author, ...]:
    with db_app.app_context():
        details = find_book_details(get_session(), book_id)
    assert details is not None
    return details.authors


def _entry(db_app: Flask, author: Author) -> AuthorEntry:
    with db_app.app_context():
        page = list_authors(get_session(), f"{author.name} {author.surname}".strip())
    (entry,) = [e for e in page.authors if e.author == author]
    return entry


def _all(db_app: Flask) -> list[tuple[Author, int]]:
    with db_app.app_context():
        page = list_authors(get_session())
    return [(e.author, e.books) for e in page.authors]


def _rename(db_app: Flask, entry: AuthorEntry, author: Author) -> Renamed:
    with db_app.app_context():
        return rename_author(get_session(), entry.id, author)


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
        session = get_session()
        found = list_authors(session, "herb")
        second = list_authors(session, "herb", page=9, page_size=1)

    assert [e.author for e in found.authors] == [Author("Brian", "Herbert"), FRANK]
    assert ([e.author for e in second.authors], second.page, second.total) == (
        [FRANK],
        2,
        2,
    )


@pytest.mark.firebird
def test_list_authors_finds_text_inside_names(db_app, user):
    """Firebird: "name surname" contains the text, as typed."""
    _book(db_app, user, "Dune", FRANK)

    with db_app.app_context():
        found = list_authors(get_session(), "rank herb")

    assert [e.author for e in found.authors] == [FRANK]


@pytest.mark.postgresql
def test_list_authors_matches_words(db_app, user):
    """PostgreSQL: each word starts a word of the name or surname, in any order."""
    _book(db_app, user, "Dune", FRANK, Author("Brian", "Herbert"))

    with db_app.app_context():
        session = get_session()
        found = list_authors(session, "herb fra")
        inside = list_authors(session, "rank")

    assert [e.author for e in found.authors] == [FRANK]
    assert inside.authors == []


def test_find_author(db_app, user):
    _book(db_app, user, "Dune", FRANK)
    entry = _entry(db_app, FRANK)

    with db_app.app_context():
        assert find_author(get_session(), entry.id) == entry
        assert find_author(get_session(), uuid4()) is None


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
        page = list_authors(get_session(), "x" * (SEARCH_MAX_LENGTH + 1))

    assert (page.authors, page.total) == ([], 0)


def test_rename_to_a_name_created_meanwhile_merges(
    db_app, user, other_conn: RawConnection, caplog
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
            (uuid4(), "Frank", "Herbert"),
        )
    # ...while this one renames Frnak to him.
    thread = threading.Thread(target=rename)
    with caplog.at_level(logging.INFO):
        thread.start()
        time.sleep(0.5)
        other_conn.commit()
        thread.join(timeout=30)

    assert results == [Renamed(FRANK, merged=True)]
    assert (
        f"Author {entry.id}: 'Frank Herbert' was created meanwhile, merging into it"
        in caplog.text
    )
    assert _authors_of(db_app, dune) == (FRANK,)
    assert _all(db_app) == [(FRANK, 1)]


def _name_taken_and_given_up(
    monkeypatch: pytest.MonkeyPatch, other_conn: RawConnection, *, times: int
) -> list[None]:
    """
    Another request creates an author with the new name just before each of
    the first `times` renames, and deletes them just after: the rename clashes
    with an author who is gone when looked up. The renames made, returned.
    """
    rename = authors_model._rename
    calls: list[None] = []

    def clash(session: Session, author_id: UUID, author: Author) -> bool:
        calls.append(None)
        if len(calls) > times:
            return rename(session, author_id, author)
        namesake = uuid4()
        with other_conn.cursor() as other:
            other.execute(
                "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
                (namesake, author.name, author.surname),
            )
        other_conn.commit()
        try:
            return rename(session, author_id, author)
        finally:
            with other_conn.cursor() as other:
                other.execute("DELETE FROM AUTHORS WHERE ID = ?", (namesake,))
            other_conn.commit()

    monkeypatch.setattr(authors_model, "_rename", clash)
    return calls


def test_rename_to_a_name_taken_and_given_up_meanwhile_tries_again(
    db_app, user, other_conn: RawConnection, monkeypatch: pytest.MonkeyPatch
):
    dune = _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)
    renames = _name_taken_and_given_up(monkeypatch, other_conn, times=1)

    assert _rename(db_app, entry, FRANK) == Renamed(FRANK, merged=False)

    assert len(renames) == 2
    assert _authors_of(db_app, dune) == (FRANK,)
    assert _all(db_app) == [(FRANK, 1)]


def test_rename_gives_up_when_the_name_keeps_being_taken_and_given_up(
    db_app, user, other_conn: RawConnection, monkeypatch: pytest.MonkeyPatch
):
    _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)
    renames = _name_taken_and_given_up(monkeypatch, other_conn, times=10)

    with pytest.raises(RuntimeError, match="kept being taken"):
        _rename(db_app, entry, FRANK)

    assert len(renames) == 10
    assert _all(db_app) == [(FRNAK, 1)]


def test_merge_keeps_an_author_a_book_being_saved_uses(
    db_app, user, other_conn: RawConnection, caplog
):
    dune = _book(db_app, user, "Dune", FRNAK)
    _book(db_app, user, "Dune Messiah", FRANK)
    emma = _book(db_app, user, "Emma")
    entry = _entry(db_app, FRNAK)
    results: list[Renamed | Exception] = []

    def rename() -> None:
        try:
            results.append(_rename(db_app, entry, FRANK))
        except Exception as e:  # noqa: BLE001 - reported by the assertion below
            results.append(e)

    # Another request gives Frnak a book, not yet committed...
    with other_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) "
            "VALUES (?, ?, 1)",
            (emma, entry.id),
        )
    # ...while this one merges Frnak into Frank Herbert.
    thread = threading.Thread(target=rename)
    with caplog.at_level(logging.INFO):
        thread.start()
        time.sleep(0.5)
        other_conn.commit()
        thread.join(timeout=30)

    assert results == [Renamed(FRANK, merged=True)]
    assert (
        f"Author {entry.id} kept after merging: a book being saved at this moment "
        "uses it" in caplog.text
    )
    assert _authors_of(db_app, dune) == (FRANK,)
    assert _authors_of(db_app, emma) == (FRNAK,)
    assert _all(db_app) == [(FRANK, 2), (FRNAK, 1)]


def test_merge_passes_on_unexpected_database_errors(
    db_app, user, fail_statement: Callable[[str], None]
):
    """Not taken for a book being saved with the author; nothing changes."""
    dune = _book(db_app, user, "Dune", FRNAK)
    _book(db_app, user, "Dune Messiah", FRANK)
    entry = _entry(db_app, FRNAK)
    fail_statement("DELETE FROM AUTHORS WHERE AUTHORS.ID")

    with pytest.raises(DBAPIError, match="deadlock"):
        _rename(db_app, entry, FRANK)

    assert _authors_of(db_app, dune) == (FRNAK,)
    assert _all(db_app) == [(FRANK, 1), (FRNAK, 1)]


def _delete_book(conn: RawConnection, book_id: UUID) -> None:
    """Delete a book (out of its collection first), left uncommitted."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM COLLECTION_BOOKS WHERE BOOK_ID = ?", (book_id,))
        cur.execute("DELETE FROM BOOKS WHERE ID = ?", (book_id,))


def _delete_book_and_author(conn: RawConnection, book_id: UUID, entry: AuthorEntry):
    """What deleting an author's last book does, left uncommitted."""
    _delete_book(conn, book_id)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM AUTHORS WHERE ID = ?", (entry.id,))


def test_rename_an_author_deleted_meanwhile(db_app, user, other_conn, race):
    dune = _book(db_app, user, "Dune", FRNAK)
    entry = _entry(db_app, FRNAK)
    _delete_book_and_author(other_conn, dune, entry)

    with pytest.raises(UnknownAuthorError):
        race(lambda session: rename_author(session, entry.id, FRANK), "UPDATE AUTHORS")

    assert _all(db_app) == []


def test_merge_an_author_deleted_meanwhile(db_app, user, other_conn, race):
    dune = _book(db_app, user, "Dune", FRNAK)
    _book(db_app, user, "Dune Messiah", FRANK)
    entry = _entry(db_app, FRNAK)
    _delete_book_and_author(other_conn, dune, entry)

    with pytest.raises(UnknownAuthorError):
        race(
            lambda session: rename_author(session, entry.id, FRANK),
            "UPDATE BOOK_AUTHORS",
        )

    assert _all(db_app) == [(FRANK, 1)]


def test_merge_an_author_while_one_of_their_books_is_deleted(
    db_app, user, other_conn, race
):
    dune = _book(db_app, user, "Dune", FRNAK)
    emma = _book(db_app, user, "Emma", FRNAK)
    _book(db_app, user, "Dune Messiah", FRANK)
    entry = _entry(db_app, FRNAK)
    _delete_book(other_conn, dune)

    renamed = race(
        lambda session: rename_author(session, entry.id, FRANK), "UPDATE BOOK_AUTHORS"
    )

    assert renamed == Renamed(FRANK, merged=True)
    assert _authors_of(db_app, emma) == (FRANK,)
    assert _all(db_app) == [(FRANK, 2)]


def test_rename_an_unknown_author(db_app):
    with db_app.app_context(), pytest.raises(UnknownAuthorError):
        rename_author(get_session(), uuid4(), FRANK)


# Routes


def test_authors_page(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Iliad", Author("", "Homer"))

    html = client.get("/authors").get_data(as_text=True)

    assert "<html" in html
    assert re.search(r">Herbert, Frank</a>\s*<span[^>]*>1 book</span>", html)
    assert ">Homer</a>" in html
    assert 'aria-label="Rename Frank Herbert">Rename</a>' in html


def test_authors_search_with_htmx_returns_only_the_list(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Emma", JANE)

    html = client.get("/authors?q=jane", headers=HTMX).get_data(as_text=True)

    assert "<html" not in html
    assert "Austen, Jane" in html
    assert "Herbert" not in html


@pytest.mark.parametrize(("size", "shown"), [(None, 10), ("50", 11)])
def test_authors_page_size(db_app, client, user, size, shown):
    for n in range(11):
        _book(db_app, user, f"Book {n:02}", Author("", f"Surname{n:02}"))

    query = {"size": size} if size else {}
    html = client.get("/authors/", query_string=query).get_data(as_text=True)

    assert len(re.findall(r'<a class="author-name"', html)) == shown
    assert '<select id="author-search-size" name="size">' in html


def test_authors_links_keep_the_page_size(db_app, client, user):
    for n in range(51):
        _book(db_app, user, f"Book {n:02}", Author("", f"Surname{n:02}"))

    html = client.get("/authors/?size=50").get_data(as_text=True)

    page_links = re.findall(r'href="(/authors/\?[^"]*page=2[^"]*)"', html)
    rename = re.search(r'href="(/authors/[0-9a-f-]+\?[^"]*)"', html)
    assert page_links and "size=50" in page_links[0]
    assert rename is not None and "size=50" in rename.group(1)


def test_rename_form_keeps_the_page_size(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    entry = _entry(db_app, FRANK)

    html = client.get(f"/authors/{entry.id}?size=50", headers=HTMX).get_data(
        as_text=True
    )

    assert '<input type="hidden" name="size" value="50">' in html


def test_authors_page_is_in_the_account_menu(client):
    html = client.get("/catalogue").get_data(as_text=True)

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
    db_app, client, user, monkeypatch: pytest.MonkeyPatch, caplog
):
    """The form was opened, then the author was merged into another one."""
    gone = AuthorEntry(uuid4(), FRNAK, 1)
    monkeypatch.setattr("bookshelf.routes.authors.find_author", lambda *_args: gone)

    with caplog.at_level(logging.INFO):
        rv = client.post(f"/authors/{gone.id}", data={"name": "A", "surname": "B"})

    assert rv.status_code == 404
    assert (
        f"Author {gone.id} ('Frnak Herbert') gone before {user.email} renamed it"
        in caplog.text
    )


# One author's books: the catalogue filtered by author (/?author=<id>)


def _titles(html: str) -> list[str]:
    return re.findall(r'<a class="book-title"[^>]*>([^<]*)</a>', html)


def test_list_books_by_one_author(db_app, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Dune Messiah", FRANK, JANE)
    _book(db_app, user, "Emma", JANE)
    frank = _entry(db_app, FRANK)

    with db_app.app_context():
        session = get_session()
        all_his = list_books(session, user.id, author_id=frank.id)
        searched = list_books(
            session, user.id, "messiah", Sort.ADDED, author_id=frank.id
        )

    assert [b.title for b in all_his.books] == ["Dune", "Dune Messiah"]
    assert all_his.total == 2
    assert [b.title for b in searched.books] == ["Dune Messiah"]


def test_author_names_link_to_their_books(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    frank = _entry(db_app, FRANK)

    html = client.get("/authors").get_data(as_text=True)

    assert (
        f'<a class="author-name" href="/catalogue?author={frank.id}">Herbert, Frank</a>'
        in html
    )


def test_an_authors_books_page(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Emma", JANE)
    frank = _entry(db_app, FRANK)

    html = client.get(f"/catalogue?author={frank.id}").get_data(as_text=True)

    assert _titles(html) == ["Dune"]
    assert "Books by Frank Herbert" in html
    assert '<a href="/catalogue">All books</a>' in html
    # The search keeps to the author.
    assert f'<input type="hidden" name="author" value="{frank.id}">' in html


def test_an_authors_books_with_htmx_search(db_app, client, user):
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Dune Messiah", FRANK)
    _book(db_app, user, "Dune: The Graphic Novel", JANE)
    frank = _entry(db_app, FRANK)

    html = client.get(f"/catalogue?author={frank.id}&q=messiah", headers=HTMX).get_data(
        as_text=True
    )

    assert "<html" not in html
    assert _titles(html) == ["Dune Messiah"]


def test_an_authors_page_links_keep_the_author(db_app, client, user):
    for n in range(51):
        _book(db_app, user, f"Book {n:02}", FRANK)
    frank = _entry(db_app, FRANK)

    html = client.get(f"/catalogue?author={frank.id}").get_data(as_text=True)

    assert f'href="/catalogue?author={frank.id}&amp;page=2"' in html
    book_link = re.search(r'<a class="book-title" href="([^"]*)"', html)
    assert book_link is not None
    assert f"author={frank.id}" in book_link.group(1)


def test_add_to_collection_from_an_authors_books(db_app, client, user, password):
    with db_app.app_context():
        other = create_user(get_session(), "other@example.com", password)
    _book(db_app, other, "Dune", FRANK)
    _book(db_app, other, "Emma", JANE)
    dune = _book(db_app, other, "Dune Messiah", FRANK)
    frank = _entry(db_app, FRANK)

    rv = client.post(
        f"/collection/books/{dune}/add",
        data={"copies": "1", "author": str(frank.id), "page": "1"},
        headers={**HTMX, "HX-Target": "book-list"},
    )

    assert _titles(rv.get_data(as_text=True)) == ["Dune", "Dune Messiah"]


def test_saving_a_book_returns_to_its_authors_books(db_app, client, user):
    dune = _book(db_app, user, "Dune", FRANK)
    frank = _entry(db_app, FRANK)
    data = {
        "authors-0-name": "Frank",
        "authors-0-surname": "Herbert",
        "author": str(frank.id),
    }

    rv = client.post(f"/books/{dune}", data=data, headers=HTMX)

    assert rv.headers["HX-Redirect"] == f"/catalogue?author={frank.id}"


@pytest.mark.parametrize(
    "author", [pytest.param(str(uuid4()), id="unknown-id"), "not-an-id"]
)
def test_unknown_authors_books_are_not_found(client, author):
    assert client.get(f"/catalogue?author={author}").status_code == 404


# The authors under each book link to their books


def test_book_lists_know_their_authors_ids(db_app, user):
    _book(db_app, user, "Dune", FRANK, JANE)
    frank, jane = _entry(db_app, FRANK), _entry(db_app, JANE)

    with db_app.app_context():
        (book,) = list_books(get_session(), user.id).books

    assert book.authors == (FRANK, JANE)  # an ID does not change equality
    assert [a.id for a in book.authors] == [frank.id, jane.id]


@pytest.mark.parametrize("url", ["/catalogue", "/"])
def test_authors_under_a_book_link_to_their_books(db_app, client, user, url):
    _book(db_app, user, "Dune", FRANK, Author("", "Homer"))
    frank, homer = _entry(db_app, FRANK), _entry(db_app, Author("", "Homer"))

    html = client.get(url).get_data(as_text=True)

    # Each list links to the author's books in that same list.
    assert (
        f'<span class="book-authors"><a href="{url}?author={frank.id}">Frank Herbert</a>, '
        f'<a href="{url}?author={homer.id}">Homer</a></span>'
    ) in html


# One author's books in the user's collection (/?author=<id>)


def test_list_collection_by_one_author(db_app, user, password):
    with db_app.app_context():
        other = create_user(get_session(), "other@example.com", password)
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Emma", JANE)
    _book(db_app, other, "Dune Messiah", FRANK)  # not in the user's collection
    frank = _entry(db_app, FRANK)

    with db_app.app_context():
        page = list_collection(get_session(), user.id, author_id=frank.id)

    assert ([b.title for b in page.books], page.total) == (["Dune"], 1)


def test_an_authors_books_in_the_collection(db_app, client, user, password):
    with db_app.app_context():
        other = create_user(get_session(), "other@example.com", password)
    _book(db_app, user, "Dune", FRANK)
    _book(db_app, user, "Emma", JANE)
    _book(db_app, other, "Dune Messiah", FRANK)
    frank = _entry(db_app, FRANK)

    html = client.get(f"/?author={frank.id}").get_data(as_text=True)

    assert _titles(html) == ["Dune"]
    assert "Your books by Frank Herbert" in html
    assert (
        f'<a href="/catalogue?author={frank.id}">All books by Frank Herbert</a>' in html
    )
    assert '<a href="/">All your books</a>' in html
    assert f'<input type="hidden" name="author" value="{frank.id}">' in html


def test_removing_without_javascript_returns_to_the_authors_books(db_app, client, user):
    dune = _book(db_app, user, "Dune", FRANK)
    frank = _entry(db_app, FRANK)

    rv = client.post(f"/collection/books/{dune}/remove", data={"author": str(frank.id)})

    assert rv.headers["Location"] == f"/?author={frank.id}"


@pytest.mark.parametrize(
    "author", [pytest.param(str(uuid4()), id="unknown-id"), "not-an-id"]
)
def test_unknown_authors_books_in_the_collection_are_not_found(client, author):
    assert client.get(f"/?author={author}").status_code == 404
