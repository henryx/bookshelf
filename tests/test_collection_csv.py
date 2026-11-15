"""Exporting the current user's collection to CSV, and importing a CSV into it."""

import csv
import io

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_db
from bookshelf.models.books import (
    Author,
    NewBook,
    add_book,
    find_book_details,
    list_collection,
    set_read,
)
from bookshelf.models.collection_csv import (
    CsvFileError,
    export_collection,
    import_collection,
)
from bookshelf.models.users import User, create_user

HTMX = {"HX-Request": "true"}
HEADER = "title,authors,isbn,publisher,year,series,volume,plot,tags,copies,read\n"


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


@pytest.fixture
def other(db_app: Flask, password: str) -> User:
    with db_app.app_context():
        return create_user(get_db(), "other@example.com", password)


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.removeprefix("﻿"))))


def _collection(app: Flask, user: User) -> list[tuple[str, int | None, bool]]:
    with app.app_context():
        books = list_collection(get_db(), user.id).books
    return [(b.title, b.copies, b.read) for b in books]


def _catalogue_size(app: Flask) -> int:
    with app.app_context(), get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM BOOKS")
        return cur.fetchone()[0]


def _import(app: Flask, user: User, text: str):
    with app.app_context():
        return import_collection(get_db(), user.id, text)


# Export


def test_export_writes_every_field_of_the_users_books(db_app, user, other):
    with db_app.app_context():
        conn = get_db()
        book_id = add_book(
            conn,
            user.id,
            NewBook(
                "Good Omens",
                (Author("Terry", "Pratchett"), Author("", "Gaiman")),
                isbn="9780552137034",
                publisher="Corgi",
                publication_year=1991,
                series="Standalone",
                volume="1",
                plot="The end of the world,\non a Saturday.",
                tags=("fantasy", "humour"),
            ),
            copies=2,
        )
        set_read(conn, user.id, book_id, read=True)
        add_book(conn, other.id, NewBook("Emma"))
        text = export_collection(conn, user.id)

    assert text.startswith("﻿")
    assert _rows(text) == [
        {
            "title": "Good Omens",
            "authors": "Pratchett, Terry; Gaiman",
            "isbn": "9780552137034",
            "publisher": "Corgi",
            "year": "1991",
            "series": "Standalone",
            "volume": "1",
            "plot": "The end of the world,\non a Saturday.",
            "tags": "fantasy, humour",
            "copies": "2",
            "read": "yes",
        }
    ]


def test_export_of_an_empty_collection_has_only_the_header(db_app, user):
    with db_app.app_context():
        text = export_collection(get_db(), user.id)

    assert text == "﻿" + HEADER.replace("\n", "\r\n")


def test_export_neutralises_spreadsheet_formulas(db_app, user):
    with db_app.app_context():
        add_book(get_db(), user.id, NewBook("=HYPERLINK(1)", plot="+cmd"))
        text = export_collection(get_db(), user.id)

    row = _rows(text)[0]
    assert (row["title"], row["plot"]) == ("'=HYPERLINK(1)", "'+cmd")


def test_export_then_import_round_trips(db_app, user, other):
    with db_app.app_context():
        conn = get_db()
        add_book(conn, user.id, NewBook("=Sum", (Author("Frank", "Herbert"),)))
        add_book(conn, user.id, NewBook("Dune", isbn="9780441013593"), copies=3)
        text = export_collection(conn, user.id)

    report = _import(db_app, other, text)

    assert (report.added, report.created, report.skipped, report.errors) == (
        2,
        0,
        0,
        [],
    )
    assert _collection(db_app, other) == [("=Sum", 1, False), ("Dune", 3, False)]


# Import


def test_import_creates_books_missing_from_the_catalogue(db_app, user):
    text = HEADER + (
        'Good Omens,"Pratchett, Terry; Gaiman, Neil",0-552-13703-0,Corgi,1991,'
        ',,A plot,"fantasy, humour",2,yes\n'
    )

    report = _import(db_app, user, text)

    assert (report.added, report.created, report.errors) == (1, 1, [])
    assert _collection(db_app, user) == [("Good Omens", 2, True)]
    with db_app.app_context():
        conn = get_db()
        book_id = list_collection(conn, user.id).books[0].id
        details = find_book_details(conn, book_id)
    assert details is not None
    assert details.authors == (Author("Terry", "Pratchett"), Author("Neil", "Gaiman"))
    assert (details.isbn, details.publisher, details.publication_year) == (
        "9780552137034",
        "Corgi",
        1991,
    )
    assert (details.plot, details.tags) == ("A plot", ("fantasy", "humour"))


def test_import_matches_a_catalogue_book_by_isbn(db_app, user, other):
    with db_app.app_context():
        add_book(get_db(), other.id, NewBook("Dune", isbn="9780441013593"))

    report = _import(db_app, user, "title,isbn,copies\nAnother title,9780441013593,2\n")

    assert (report.added, report.created) == (1, 0)
    assert _collection(db_app, user) == [("Dune", 2, False)]
    assert _catalogue_size(db_app) == 1


def test_import_without_isbn_matches_title_and_authors_ignoring_case(
    db_app, user, other
):
    with db_app.app_context():
        conn = get_db()
        add_book(conn, other.id, NewBook("Dune", (Author("Frank", "Herbert"),)))
        add_book(conn, other.id, NewBook("Emma", (Author("Jane", "Austen"),)))

    report = _import(
        db_app, user, 'title,authors\nDUNE,"herbert, frank"\nEmma,"Brontë, Emily"\n'
    )

    assert (report.added, report.created) == (2, 1)
    assert _catalogue_size(db_app) == 3
    assert sorted(_collection(db_app, user)) == [
        ("Dune", 1, False),
        ("Emma", 1, False),
    ]


def test_import_skips_books_already_in_the_collection(db_app, user):
    with db_app.app_context():
        add_book(get_db(), user.id, NewBook("Dune", isbn="9780441013593"), copies=4)

    report = _import(
        db_app, user, "title,isbn,copies,read\nDune,9780441013593,1,yes\nDune,,,\n"
    )

    assert (report.added, report.skipped) == (0, 2)
    assert _collection(db_app, user) == [("Dune", 4, False)]


def test_import_adds_a_book_repeated_in_the_file_once(db_app, user):
    report = _import(db_app, user, 'title,authors\nDune,Herbert\ndune,"herbert"\n')

    assert (report.created, report.skipped) == (1, 1)
    assert _catalogue_size(db_app) == 1


def test_import_reports_bad_rows_and_imports_the_rest(db_app, user):
    text = HEADER + (
        ",Herbert,,,,,,,,,\n"
        ",,,,,,,,,,\n"
        "Dune,,123,,,,,,,,\n"
        "Emma,,,,year,,,,,,\n"
        "Emma,,,,,,,,,0,\n"
        "Emma,,,,,,,,,,maybe\n"
        'Emma,", Jane",,,,,,,,,\n'
        "Persuasion,,,,,,,,,,no\n"
    )

    report = _import(db_app, user, text)

    assert report.added == 1
    assert report.errors == [
        "Row 2: the title is required.",
        "Row 4: not a valid ISBN.",
        "Row 5: the year must be a whole number from 1 to 9999.",
        "Row 6: copies must be a whole number from 1 to 999.",
        "Row 7: read must be yes or no.",
        "Row 8: each author needs a surname (“Surname, Name”).",
    ]
    assert _collection(db_app, user) == [("Persuasion", 1, False)]


def test_import_reports_values_too_long(db_app, user):
    report = _import(db_app, user, f"title,tags\n{'x' * 256},\nDune,{'t' * 65}\n")

    assert report.errors == [
        "Row 2: the title cannot be longer than 255 characters.",
        "Row 3: tags cannot be longer than 64 characters.",
    ]


def test_import_strips_the_formula_guard(db_app, user):
    report = _import(db_app, user, "title\n'=Sum\n'Tis\n")

    assert report.created == 2
    assert sorted(_collection(db_app, user)) == [("'Tis", 1, False), ("=Sum", 1, False)]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "The file is empty."),
        ("authors\nHerbert\n", "The file has no title column."),
        ("title,colour\nDune,red\n", "Unknown column: colour."),
        ("title,title\nDune,Dune\n", "Column title is repeated."),
    ],
)
def test_import_refuses_a_file_it_cannot_read(db_app, user, text, message):
    with pytest.raises(CsvFileError, match=message):
        _import(db_app, user, text)


def test_import_refuses_too_many_rows(db_app, user, monkeypatch):
    monkeypatch.setattr("bookshelf.models.collection_csv.MAX_ROWS", 2)

    with pytest.raises(CsvFileError, match="more than 2 books"):
        _import(db_app, user, "title\nA\nB\nC\n")

    assert _collection(db_app, user) == []


# Routes


def test_export_route_downloads_the_csv(client, db_app, user):
    with db_app.app_context():
        add_book(get_db(), user.id, NewBook("Dune"))

    rv = client.get("/account/export")

    assert rv.status_code == 200
    assert rv.mimetype == "text/csv"
    assert rv.headers["Content-Disposition"].startswith(
        'attachment; filename="bookshelf-collection-'
    )
    assert [r["title"] for r in _rows(rv.get_data(as_text=True))] == ["Dune"]


def test_export_needs_login(db_client):
    rv = db_client.get("/account/export")

    assert rv.status_code == 302
    assert "/login" in rv.headers["Location"]


def _upload(client: FlaskClient, text: str | bytes, headers=None, name="books.csv"):
    data = text.encode() if isinstance(text, str) else text
    return client.post(
        "/account/import",
        data={"file": (io.BytesIO(data), name)},
        headers=headers or {},
        content_type="multipart/form-data",
    )


def test_import_route_with_htmx_shows_the_report(client, db_app, user):
    rv = _upload(client, "title,copies\nDune,2\n,3\n", headers=HTMX)

    assert rv.status_code == 200
    html = rv.get_data(as_text=True)
    assert "<html" not in html
    assert "1 book added to your collection (1 new to the catalogue)." in html
    assert "Row 3: the title is required." in html
    assert 'id="collection-csv"' in html
    assert _collection(db_app, user) == [("Dune", 2, False)]


def test_import_route_without_javascript_shows_the_account_page(client, db_app, user):
    rv = _upload(client, "title\nDune\n")

    assert rv.status_code == 200
    html = rv.get_data(as_text=True)
    assert "<html" in html
    assert 'id="account-form"' in html
    assert "1 book added to your collection (1 new to the catalogue)." in html


def test_import_route_reports_skipped_books(client, db_app, user):
    with db_app.app_context():
        add_book(get_db(), user.id, NewBook("Dune"))

    rv = _upload(client, "title\nDune\n", headers=HTMX)

    assert "No books added; 1 already in your collection, skipped." in rv.get_data(
        as_text=True
    )


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (b"title,colour\nDune,red\n", "books.csv", "Unknown column: colour."),
        (b"title\n\xff\xfe\n", "books.csv", "The file is not UTF-8 text."),
        (b"title\nDune\n", "books.txt", "Choose a .csv file."),
    ],
)
def test_import_route_refuses_bad_files(client, db_app, user, data, name, message):
    rv = _upload(client, data, headers=HTMX, name=name)

    assert rv.status_code == 200
    assert message in rv.get_data(as_text=True)
    assert _collection(db_app, user) == []


def test_import_route_without_a_file(client):
    rv = client.post("/account/import", data={}, headers=HTMX)

    assert "Choose a CSV file to import." in rv.get_data(as_text=True)


def test_import_route_refuses_files_too_large(client, app):
    app.config["MAX_CONTENT_LENGTH"] = 100

    rv = _upload(client, "title\n" + "Dune\n" * 100)

    assert rv.status_code == 413


def test_account_page_offers_import_and_export(client):
    html = client.get("/account").get_data(as_text=True)

    assert 'href="/account/export"' in html
    assert 'action="/account/import"' in html
    assert 'enctype="multipart/form-data"' in html


def test_collection_page_has_no_import_or_export(client):
    html = client.get("/collection").get_data(as_text=True)

    assert "/account/export" not in html
    assert "/account/import" not in html


def test_import_does_not_touch_other_users(db_app, user, other):
    with db_app.app_context():
        add_book(get_db(), other.id, NewBook("Dune"), copies=5)

    _import(db_app, user, "title,copies\nDune,1\n")

    assert _collection(db_app, other) == [("Dune", 5, False)]
