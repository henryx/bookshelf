"""Deleting a book: admins only, and only while it is in nobody's collection."""

import logging
import re
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.authors import list_authors
from bookshelf.models.books import (
    Author,
    BookInCollectionError,
    DeletedBook,
    NewBook,
    UnknownBookError,
    add_book,
    add_to_collection,
    delete_book,
    find_book_details,
    remove_from_collection,
)
from bookshelf.models.users import Role, User, create_user

HTMX = {"HX-Request": "true"}
FRANK = Author("Frank", "Herbert")
JANE = Author("Jane", "Austen")


@pytest.fixture
def admin(db_app: Flask, password: str) -> User:
    with db_app.app_context():
        return create_user(get_db(), "boss@example.com", password, role=Role.ADMIN)


def _log_in(client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return client


@pytest.fixture
def admin_client(db_client: FlaskClient, admin: User, password: str) -> FlaskClient:
    return _log_in(db_client, admin, password)


@pytest.fixture
def reader_client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    return _log_in(db_client, user, password)


def _book(db_app: Flask, user: User, book: NewBook, *, keep: bool = False) -> UUID:
    """A catalogue book; in nobody's collection unless `keep`."""
    with db_app.app_context():
        conn = get_db()
        book_id = add_book(conn, user.id, book)
        if not keep:
            remove_from_collection(conn, user.id, book_id)
    return book_id


def _exists(db_app: Flask, book_id: UUID) -> bool:
    with db_app.app_context():
        return find_book_details(get_db(), book_id) is not None


def _authors(db_app: Flask) -> list[Author]:
    with db_app.app_context():
        return [e.author for e in list_authors(get_db()).authors]


# Model


def test_delete_book_removes_it_and_authors_left_without_books(db_app, user):
    dune = _book(
        db_app, user, NewBook("Dune", authors=(FRANK, JANE), categories=("sf",))
    )
    _book(db_app, user, NewBook("Emma", authors=(JANE,)))

    with db_app.app_context():
        (frank,) = list_authors(get_db(), "frank").authors
        deleted = delete_book(get_db(), dune)

    assert deleted == DeletedBook("Dune", authors_deleted=(frank.id,))
    assert not _exists(db_app, dune)
    assert _authors(db_app) == [JANE]  # Frank Herbert had only Dune


def test_book_in_a_collection_cannot_be_deleted(db_app, user):
    dune = _book(db_app, user, NewBook("Dune"), keep=True)

    with db_app.app_context(), pytest.raises(BookInCollectionError):
        delete_book(get_db(), dune)

    assert _exists(db_app, dune)


def test_delete_an_unknown_book(db_app):
    with db_app.app_context(), pytest.raises(UnknownBookError):
        delete_book(get_db(), uuid4())


def test_delete_a_book_deleted_meanwhile_is_unknown(db_app, user, other_conn, race):
    dune = _book(db_app, user, NewBook("Dune", authors=(FRANK,)))
    # Another admin deletes it, not yet committed.
    with other_conn.cursor() as cur:
        cur.execute("DELETE FROM BOOKS WHERE ID = ?", (dune.bytes,))

    with pytest.raises(UnknownBookError):
        race(lambda conn: delete_book(conn, dune), "DELETE FROM BOOKS")


def test_delete_a_book_whose_author_is_removed_meanwhile(
    db_app, user, other_conn, race
):
    dune = _book(db_app, user, NewBook("Dune", authors=(FRANK,)))
    # Someone editing it removes its author, not yet committed.
    with other_conn.cursor() as cur:
        cur.execute("DELETE FROM BOOK_AUTHORS WHERE BOOK_ID = ?", (dune.bytes,))

    deleted = race(lambda conn: delete_book(conn, dune), "DELETE FROM BOOKS")

    assert deleted == DeletedBook("Dune", ())
    assert not _exists(db_app, dune)


def test_book_details_count_the_collections_holding_it(db_app, user, admin):
    dune = _book(db_app, user, NewBook("Dune"), keep=True)
    with db_app.app_context():
        add_to_collection(get_db(), admin.id, dune)
        details = find_book_details(get_db(), dune)

    assert details is not None
    assert details.collections == 2


# Routes


def test_admin_sees_delete_for_a_book_in_no_collection(db_app, admin_client, user):
    dune = _book(db_app, user, NewBook("Dune"))

    html = admin_client.get(f"/books/{dune}", headers=HTMX).get_data(as_text=True)

    assert f'hx-post="/books/{dune}/delete"' in html
    assert 'hx-confirm="Delete “Dune”? This cannot be undone."' in html


def test_admin_is_told_why_a_book_in_a_collection_cannot_be_deleted(
    db_app, admin_client, user
):
    dune = _book(db_app, user, NewBook("Dune"), keep=True)

    html = admin_client.get(f"/books/{dune}", headers=HTMX).get_data(as_text=True)

    assert "/delete" not in html
    assert "can be deleted only when it is in nobody’s collection (now 1)" in html


def test_regular_users_cannot_delete(db_app, reader_client, user):
    dune = _book(db_app, user, NewBook("Dune"))

    html = reader_client.get(f"/books/{dune}", headers=HTMX).get_data(as_text=True)
    rv = reader_client.post(f"/books/{dune}/delete")

    assert "Delete book" not in html
    assert rv.status_code == 403
    assert _exists(db_app, dune)


def test_delete_returns_to_the_list(db_app, admin_client, user, admin, caplog):
    dune = _book(db_app, user, NewBook("Dune"))

    with caplog.at_level(logging.INFO):
        rv = admin_client.post(
            f"/books/{dune}/delete",
            data={"list": "", "q": "du", "sort": "added", "page": "1"},
            headers=HTMX,
        )

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/catalogue?q=du&sort=added"
    assert not _exists(db_app, dune)
    assert "Book “Dune” deleted." in admin_client.get("/catalogue").get_data(
        as_text=True
    )
    assert f"Book 'Dune' deleted by {admin.email}" in caplog.text


def test_delete_drops_the_filter_of_an_author_now_gone(db_app, admin_client, user):
    dune = _book(db_app, user, NewBook("Dune", authors=(FRANK,)))
    _book(db_app, user, NewBook("Emma", authors=(JANE,)))
    with db_app.app_context():
        (frank,) = list_authors(get_db(), "frank").authors

    rv = admin_client.post(f"/books/{dune}/delete", data={"author": str(frank.id)})

    assert rv.headers["Location"] == "/catalogue"


def test_delete_keeps_the_filter_of_an_author_still_there(db_app, admin_client, user):
    dune = _book(db_app, user, NewBook("Dune", authors=(FRANK,)))
    _book(db_app, user, NewBook("Dune Messiah", authors=(FRANK,)))
    with db_app.app_context():
        (frank,) = list_authors(get_db(), "frank").authors

    rv = admin_client.post(f"/books/{dune}/delete", data={"author": str(frank.id)})

    assert rv.headers["Location"] == f"/catalogue?author={frank.id}"


def test_delete_refused_for_a_book_added_to_a_collection_meanwhile(
    db_app, admin_client, user, admin, caplog
):
    dune = _book(db_app, user, NewBook("Dune"), keep=True)

    with caplog.at_level(logging.INFO):
        rv = admin_client.post(f"/books/{dune}/delete", headers=HTMX)

    assert rv.status_code == 409
    assert rv.headers["HX-Retarget"] == "#error-banner"
    assert re.search(r"“Dune” is in someone’s collection", rv.get_data(as_text=True))
    assert _exists(db_app, dune)
    assert f"Delete of book {dune} by {admin.email} refused: it is in a collection" in (
        caplog.text
    )


def test_delete_an_unknown_book_is_not_found(admin_client, admin, caplog):
    book_id = uuid4()

    with caplog.at_level(logging.INFO):
        rv = admin_client.post(f"/books/{book_id}/delete")

    assert rv.status_code == 404
    assert f"Delete of unknown book {book_id} by {admin.email}" in caplog.text
