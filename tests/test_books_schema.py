"""Rules the database itself enforces on the books tables."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from firebird.driver import Connection, DatabaseError
from flask import Flask

from bookshelf.db import get_db
from bookshelf.models.users import User


@pytest.fixture
def conn(db_app: Flask) -> Iterator[Connection]:
    with db_app.app_context():
        yield get_db()


def _run(conn: Connection, sql: str, params: tuple[object, ...] = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()


def _refused(conn: Connection, sql: str, params: tuple[object, ...] = ()) -> bool:
    try:
        _run(conn, sql, params)
    except DatabaseError:
        conn.rollback()
        return True
    return False


def _count(conn: Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        (count,) = cur.fetchone()
    return count


def _add_book(
    conn: Connection, title: str = "A book", isbn: str | None = None
) -> bytes:
    book_id = uuid4().bytes
    _run(
        conn,
        "INSERT INTO BOOKS (ID, TITLE, ISBN) VALUES (?, ?, ?)",
        (book_id, title, isbn),
    )
    return book_id


def _add_author(conn: Connection, surname: str, name: str = "") -> bytes:
    author_id = uuid4().bytes
    _run(
        conn,
        "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
        (author_id, name, surname),
    )
    return author_id


def _add_to_collection(conn: Connection, user: User, book_id: bytes) -> None:
    _run(
        conn,
        "INSERT INTO COLLECTION_BOOKS (USER_ID, BOOK_ID) VALUES (?, ?)",
        (user.id.bytes, book_id),
    )


def test_books_without_isbn_are_allowed(conn: Connection):
    _add_book(conn)
    _add_book(conn)

    assert _count(conn, "SELECT COUNT(*) FROM BOOKS WHERE ISBN IS NULL") == 2


def test_duplicate_isbn_is_refused(conn: Connection):
    _add_book(conn, isbn="9788804123456")

    assert _refused(
        conn,
        "INSERT INTO BOOKS (ID, TITLE, ISBN) VALUES (?, ?, ?)",
        (uuid4().bytes, "Another", "9788804123456"),
    )


@pytest.mark.parametrize("isbn", ["978-8804123456", "880412345X", "97888041234567"])
def test_isbn_must_be_13_digits(conn: Connection, isbn: str):
    assert _refused(
        conn,
        "INSERT INTO BOOKS (ID, TITLE, ISBN) VALUES (?, ?, ?)",
        (uuid4().bytes, "A book", isbn),
    )


@pytest.mark.parametrize("title", ["", "   ", " Untrimmed", "Untrimmed "])
def test_title_must_be_non_blank_and_trimmed(conn: Connection, title: str):
    assert _refused(
        conn, "INSERT INTO BOOKS (ID, TITLE) VALUES (?, ?)", (uuid4().bytes, title)
    )


def test_date_added_is_set_automatically(conn: Connection):
    _add_book(conn)

    assert _count(conn, "SELECT COUNT(*) FROM BOOKS WHERE CREATED_AT IS NULL") == 0


def test_publishers_match_ignoring_case(conn: Connection):
    _run(
        conn,
        "INSERT INTO BOOKS (ID, TITLE, PUBLISHER) VALUES (?, ?, ?)",
        (uuid4().bytes, "Mort", "Gollancz"),
    )

    assert (
        _count(conn, "SELECT COUNT(*) FROM BOOKS WHERE PUBLISHER = ?", ("GOLLANCZ",))
        == 1
    )


def test_titles_sort_and_match_ignoring_case(conn: Connection):
    for title in ("zebra", "Apple", "Mango"):
        _add_book(conn, title)

    with conn.cursor() as cur:
        cur.execute("SELECT TITLE FROM BOOKS ORDER BY TITLE")
        titles = [title for (title,) in cur.fetchall()]

    assert titles == ["Apple", "Mango", "zebra"]
    assert _count(conn, "SELECT COUNT(*) FROM BOOKS WHERE TITLE = ?", ("APPLE",)) == 1


def test_long_accented_names_fit_their_indexes(conn: Connection):
    _add_book(conn, "è" * 255)
    _add_author(conn, "é" * 127, "ü" * 127)

    assert _count(conn, "SELECT COUNT(*) FROM AUTHORS") == 1


def test_author_name_and_surname_are_unique_ignoring_case(conn: Connection):
    _add_author(conn, "Calvino", "Italo")

    assert _refused(
        conn,
        "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
        (uuid4().bytes, "italo", "CALVINO"),
    )


def test_authors_may_share_a_surname(conn: Connection):
    _add_author(conn, "Brontë", "Charlotte")
    _add_author(conn, "Brontë", "Emily")
    _add_author(conn, "Brontë")

    assert _count(conn, "SELECT COUNT(*) FROM AUTHORS") == 3


def test_author_without_name_is_unique_too(conn: Connection):
    _add_author(conn, "Homer")

    assert _refused(
        conn,
        "INSERT INTO AUTHORS (ID, SURNAME) VALUES (?, ?)",
        (uuid4().bytes, "homer"),
    )


@pytest.mark.parametrize("surname", ["", " Untrimmed", "Untrimmed "])
def test_author_surname_must_be_non_blank_and_trimmed(conn: Connection, surname: str):
    assert _refused(
        conn,
        "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
        (uuid4().bytes, "Italo", surname),
    )


@pytest.mark.parametrize("name", [" Untrimmed", "Untrimmed ", None])
def test_author_name_must_be_trimmed_and_not_null(conn: Connection, name: str | None):
    assert _refused(
        conn,
        "INSERT INTO AUTHORS (ID, NAME, SURNAME) VALUES (?, ?, ?)",
        (uuid4().bytes, name, "Calvino"),
    )


def test_author_appears_once_per_book(conn: Connection):
    book_id = _add_book(conn)
    author_id = _add_author(conn, "Calvino", "Italo")
    _run(
        conn,
        "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) VALUES (?, ?, 1)",
        (book_id, author_id),
    )

    assert _refused(
        conn,
        "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) VALUES (?, ?, 2)",
        (book_id, author_id),
    )


def test_two_authors_cannot_share_a_place_in_the_order(conn: Connection):
    book_id = _add_book(conn)
    first = _add_author(conn, "First")
    second = _add_author(conn, "Second")
    _run(
        conn,
        "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) VALUES (?, ?, 1)",
        (book_id, first),
    )

    assert _refused(
        conn,
        "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) VALUES (?, ?, 1)",
        (book_id, second),
    )


def test_author_order_starts_at_one(conn: Connection):
    assert _refused(
        conn,
        "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) VALUES (?, ?, 0)",
        (_add_book(conn), _add_author(conn, "Calvino", "Italo")),
    )


def test_author_of_a_book_cannot_be_deleted(conn: Connection):
    book_id = _add_book(conn)
    author_id = _add_author(conn, "Calvino", "Italo")
    _run(
        conn,
        "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) VALUES (?, ?, 1)",
        (book_id, author_id),
    )

    assert _refused(conn, "DELETE FROM AUTHORS WHERE ID = ?", (author_id,))


def test_categories_are_unique_per_book_ignoring_case(conn: Connection):
    book_id = _add_book(conn)
    _run(
        conn,
        "INSERT INTO BOOK_CATEGORIES (BOOK_ID, CATEGORY) VALUES (?, ?)",
        (book_id, "Sci-fi"),
    )

    assert _refused(
        conn,
        "INSERT INTO BOOK_CATEGORIES (BOOK_ID, CATEGORY) VALUES (?, ?)",
        (book_id, "sci-FI"),
    )
    assert (
        _count(
            conn, "SELECT COUNT(*) FROM BOOK_CATEGORIES WHERE CATEGORY = ?", ("SCI-FI",)
        )
        == 1
    )


@pytest.mark.parametrize("category", ["", " Untrimmed", "Untrimmed "])
def test_category_must_be_non_blank_and_trimmed(conn: Connection, category: str):
    assert _refused(
        conn,
        "INSERT INTO BOOK_CATEGORIES (BOOK_ID, CATEGORY) VALUES (?, ?)",
        (_add_book(conn), category),
    )


def test_collection_holds_one_copy_by_default(conn: Connection, user: User):
    _add_to_collection(conn, user, _add_book(conn))

    assert _count(conn, "SELECT COPIES FROM COLLECTION_BOOKS") == 1


def test_collection_needs_at_least_one_copy(conn: Connection, user: User):
    _add_to_collection(conn, user, _add_book(conn))

    assert _refused(conn, "UPDATE COLLECTION_BOOKS SET COPIES = 0")


def test_book_in_a_collection_cannot_be_deleted(conn: Connection, user: User):
    book_id = _add_book(conn)
    _add_to_collection(conn, user, book_id)

    assert _refused(conn, "DELETE FROM BOOKS WHERE ID = ?", (book_id,))


def test_deleting_a_user_empties_their_collection(conn: Connection, user: User):
    book_id = _add_book(conn)
    _add_to_collection(conn, user, book_id)

    _run(conn, "DELETE FROM USERS WHERE ID = ?", (user.id.bytes,))

    assert _count(conn, "SELECT COUNT(*) FROM COLLECTION_BOOKS") == 0
    assert _count(conn, "SELECT COUNT(*) FROM BOOKS WHERE ID = ?", (book_id,)) == 1


def test_deleting_a_book_removes_its_author_and_category_links(conn: Connection):
    book_id = _add_book(conn)
    author_id = _add_author(conn, "Calvino", "Italo")
    _run(
        conn,
        "INSERT INTO BOOK_AUTHORS (BOOK_ID, AUTHOR_ID, SORT_ORDER) VALUES (?, ?, 1)",
        (book_id, author_id),
    )
    _run(
        conn,
        "INSERT INTO BOOK_CATEGORIES (BOOK_ID, CATEGORY) VALUES (?, ?)",
        (book_id, "Novel"),
    )

    _run(conn, "DELETE FROM BOOKS WHERE ID = ?", (book_id,))

    assert _count(conn, "SELECT COUNT(*) FROM BOOK_AUTHORS") == 0
    assert _count(conn, "SELECT COUNT(*) FROM BOOK_CATEGORIES") == 0
    assert _count(conn, "SELECT COUNT(*) FROM AUTHORS") == 1
