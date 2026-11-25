"""Scores: each user scores the books in their collection, 1 to 5 stars."""

import re
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient
from raw_db import raw
from sqlalchemy.exc import DBAPIError

from bookshelf import create_app
from bookshelf.db import get_session
from bookshelf.models.books import (
    CopyMode,
    NewBook,
    add_book,
    add_to_collection,
    copy_collection,
    find_book,
    list_books,
    list_collection,
    remove_from_collection,
    set_score,
)
from bookshelf.models.users import User, create_user

FROM_LIST = {"HX-Request": "true", "HX-Target": "book-list"}


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


@pytest.fixture
def other(db_app: Flask, password: str) -> User:
    with db_app.app_context():
        return create_user(get_session(), "other@example.com", password)


def _book(app: Flask, owner: User, title: str) -> UUID:
    """A catalogue book, in `owner`'s collection."""
    with app.app_context():
        return add_book(get_session(), owner.id, NewBook(title))


def _score(app: Flask, user: User, book_id: UUID, score: int | None) -> bool:
    with app.app_context():
        return set_score(get_session(), user.id, book_id, score)


def _scores(app: Flask, user: User) -> dict[str, int | None]:
    """The user's score of each book in their collection, by title."""
    with app.app_context():
        return {b.title: b.score for b in list_collection(get_session(), user.id).books}


def _medians(app: Flask, user: User) -> dict[str, tuple[float | None, int]]:
    """Each catalogue book's median score and number of scores, by title."""
    with app.app_context():
        return {
            b.title: (b.median_score, b.scores)
            for b in list_books(get_session(), user.id).books
        }


# Scoring


def test_books_start_without_a_score(db_app, user):
    _book(db_app, user, "Dune")

    assert _scores(db_app, user) == {"Dune": None}


def test_set_score_is_per_user(db_app, user, other):
    dune = _book(db_app, user, "Dune")
    with db_app.app_context():
        add_to_collection(get_session(), other.id, dune)

    assert _score(db_app, user, dune, 4)
    assert _score(db_app, other, dune, 2)

    assert _scores(db_app, user) == {"Dune": 4}
    assert _scores(db_app, other) == {"Dune": 2}
    with db_app.app_context():
        found = find_book(get_session(), user.id, dune)
    assert found is not None and found.score == 4


def test_set_score_none_clears_it(db_app, user):
    dune = _book(db_app, user, "Dune")
    _score(db_app, user, dune, 5)

    assert _score(db_app, user, dune, None)

    assert _scores(db_app, user) == {"Dune": None}


def test_set_score_of_a_book_not_in_the_collection(db_app, user, other):
    emma = _book(db_app, other, "Emma")

    assert not _score(db_app, user, emma, 3)


@pytest.mark.parametrize("score", [0, 6])
def test_set_score_out_of_range_is_refused(db_app, user, score):
    dune = _book(db_app, user, "Dune")

    with pytest.raises(ValueError, match="1 to 5"):
        _score(db_app, user, dune, score)


@pytest.mark.parametrize("score", [0, 6])
def test_the_database_refuses_a_score_out_of_range(db_app, user, score):
    dune = _book(db_app, user, "Dune")

    with db_app.app_context():
        conn = raw(get_session())
        with pytest.raises(DBAPIError), conn.cursor() as cur:
            cur.execute(
                "UPDATE COLLECTION_BOOKS SET SCORE = ? WHERE BOOK_ID = ?",
                (score, dune),
            )


def test_removing_a_book_removes_its_score(db_app, user):
    dune = _book(db_app, user, "Dune")
    _score(db_app, user, dune, 5)

    with db_app.app_context():
        session = get_session()
        assert remove_from_collection(session, user.id, dune)
        assert add_to_collection(session, user.id, dune)

    assert _scores(db_app, user) == {"Dune": None}


# The catalogue's median


def test_median_of_an_odd_number_of_scores(db_app, user, other, password):
    dune = _book(db_app, user, "Dune")
    with db_app.app_context():
        third = create_user(get_session(), "third@example.com", password)
        add_to_collection(get_session(), other.id, dune)
        add_to_collection(get_session(), third.id, dune)
    _score(db_app, user, dune, 5)
    _score(db_app, other, dune, 1)
    _score(db_app, third, dune, 4)

    assert _medians(db_app, user) == {"Dune": (4, 3)}


def test_median_of_an_even_number_of_scores_may_be_a_half(db_app, user, other):
    dune = _book(db_app, user, "Dune")
    with db_app.app_context():
        add_to_collection(get_session(), other.id, dune)
    _score(db_app, user, dune, 3)
    _score(db_app, other, dune, 4)

    assert _medians(db_app, user) == {"Dune": (3.5, 2)}


def test_median_counts_only_the_scored_books(db_app, user, other):
    dune = _book(db_app, user, "Dune")
    _book(db_app, user, "Emma")
    with db_app.app_context():
        add_to_collection(get_session(), other.id, dune)
    _score(db_app, user, dune, 2)

    assert _medians(db_app, other) == {"Dune": (2, 1), "Emma": (None, 0)}


# The collection page


def _stars(html: str, title: str) -> list[str]:
    """The star buttons of a book's row."""
    return re.findall(
        rf'<button [^>]*name="score"[^>]*aria-label="[^"]*{title}[^"]*"[^>]*>', html
    )


def test_collection_rows_have_five_stars(db_app, client, user):
    dune = _book(db_app, user, "Dune")
    _score(db_app, user, dune, 3)

    stars = _stars(client.get("/").get_data(as_text=True), "Dune")

    assert [re.findall(r'value="(\d)"', s) for s in stars] == [[n] for n in "12345"]
    pressed = [s for s in stars if 'aria-pressed="true"' in s]
    assert len(pressed) == 1 and 'value="3"' in pressed[0]
    assert [" on" in s for s in stars] == [True, True, True, False, False]


def test_score_a_book_from_the_list(db_app, client, user):
    dune = _book(db_app, user, "Dune")

    rv = client.post(
        f"/collection/books/{dune}/score", data={"score": "4"}, headers=FROM_LIST
    )
    html = rv.get_data(as_text=True)

    assert "“Dune” scored 4 of 5." in html
    assert 'value="4"' in next(s for s in _stars(html, "Dune") if "true" in s)
    assert _scores(db_app, user) == {"Dune": 4}


def test_clicking_the_score_again_clears_it(db_app, client, user):
    dune = _book(db_app, user, "Dune")
    _score(db_app, user, dune, 4)

    rv = client.post(
        f"/collection/books/{dune}/score", data={"score": "4"}, headers=FROM_LIST
    )

    assert "Score of “Dune” cleared." in rv.get_data(as_text=True)
    assert _scores(db_app, user) == {"Dune": None}


def test_score_without_javascript(db_app, client, user):
    dune = _book(db_app, user, "Dune")

    rv = client.post(f"/collection/books/{dune}/score", data={"score": "2", "q": "du"})

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/?q=du"
    assert _scores(db_app, user) == {"Dune": 2}
    assert "“Dune” scored 2 of 5." in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("score", ["0", "6", "abc", ""])
def test_invalid_score_is_refused(db_app, client, user, score):
    dune = _book(db_app, user, "Dune")

    rv = client.post(f"/collection/books/{dune}/score", data={"score": score})

    assert rv.status_code == 400
    assert _scores(db_app, user) == {"Dune": None}


def test_score_a_book_not_in_the_collection_says_so(db_app, client, user, other):
    emma = _book(db_app, other, "Emma")

    rv = client.post(
        f"/collection/books/{emma}/score", data={"score": "3"}, headers=FROM_LIST
    )

    assert re.search(
        r'class="error"[^>]*>“Emma” is not in your collection.',
        rv.get_data(as_text=True),
    )


def test_score_an_unknown_book_is_not_found(client):
    rv = client.post(f"/collection/books/{uuid4()}/score", data={"score": "3"})

    assert rv.status_code == 404


def test_score_requires_csrf_token(db_app, user):
    client = create_app({"TESTING": True}).test_client()

    rv = client.post(f"/collection/books/{uuid4()}/score", data={"score": "3"})

    assert rv.status_code == 400


# The catalogue page


def test_the_catalogue_shows_the_median_score(db_app, client, user, other):
    dune = _book(db_app, user, "Dune")
    _book(db_app, user, "Emma")
    with db_app.app_context():
        add_to_collection(get_session(), other.id, dune)
    _score(db_app, user, dune, 3)
    _score(db_app, other, dune, 4)

    html = client.get("/catalogue").get_data(as_text=True)

    assert 'aria-label="Median score 3.5 of 5, from 2 scores"' in html
    assert re.findall(r'class="star (full|half|empty)"', html) == [
        "full",
        "full",
        "full",
        "half",
        "empty",
    ]
    assert 'name="score"' not in html


def test_the_catalogue_says_when_a_book_has_no_scores(db_app, client, user):
    _book(db_app, user, "Emma")

    html = client.get("/catalogue").get_data(as_text=True)

    assert "No scores" in html
    assert "Median score" not in html


# Copying a collection


def test_copy_collection_copies_the_scores(db_app, user, other):
    dune = _book(db_app, user, "Dune")
    emma = _book(db_app, user, "Emma")
    _score(db_app, user, dune, 5)
    with db_app.app_context():
        add_to_collection(get_session(), other.id, emma)
    _score(db_app, other, emma, 1)

    with db_app.app_context():
        copy_collection(get_session(), user.id, other.id, CopyMode.KEEP)

    # Dune copied with its score; Emma, already the target's, keeps theirs.
    assert _scores(db_app, other) == {"Dune": 5, "Emma": 1}


def test_copy_collection_replacing_copies_the_scores(db_app, user, other):
    emma = _book(db_app, user, "Emma")
    with db_app.app_context():
        add_to_collection(get_session(), other.id, emma)
    _score(db_app, user, emma, 3)
    _score(db_app, other, emma, 1)

    with db_app.app_context():
        copy_collection(get_session(), user.id, other.id, CopyMode.REPLACE)

    assert _scores(db_app, other) == {"Emma": 3}
