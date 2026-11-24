"""
Scanning a book's barcode with the camera into the add-book form: the page
side (the Scan button, its dialog and the vendored decoder). Scanning itself
is static/js/scan.js, tested in tests/e2e/test_scan_ui.py.
"""

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient

from bookshelf.db import get_session
from bookshelf.models.books import NewBook, add_book
from bookshelf.models.users import User, create_user

HTMX = {"HX-Request": "true"}
DECODER = "/static/zxing/zxing.min.js"


@pytest.fixture
def client(db_client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = db_client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return db_client


@pytest.mark.parametrize("path", ["/books/new", "/catalogue", "/"])
def test_add_form_has_a_scan_button_and_its_dialog(client, path):
    html = client.get(path).get_data(as_text=True)

    button = re.search(r"<button [^>]*data-barcode-scan[^>]*>", html)
    assert button
    assert 'type="button"' in button[0]
    assert 'aria-controls="book-scan-dialog"' in button[0]
    dialog = re.search(r'<dialog [^>]*id="book-scan-dialog"[^>]*>', html)
    assert dialog
    assert 'data-barcode-input="book-isbn"' in dialog[0]
    assert f'data-barcode-decoder="{DECODER}"' in dialog[0]
    assert " open" not in dialog[0]


def test_a_picked_book_has_no_scan_button(db_app: Flask, client, password):
    with db_app.app_context():
        other = create_user(get_session(), "other@example.com", password)
        book_id = add_book(
            get_session(), other.id, NewBook("Dune", isbn="9780441172719")
        )

    html = client.get(f"/books/new?book={book_id}", headers=HTMX).get_data(as_text=True)

    assert "data-barcode-scan" not in html
    assert "book-scan-dialog" not in html


def test_the_decoder_and_its_licence_are_served(client):
    decoder = client.get(DECODER)
    licence = client.get("/static/zxing/LICENSE")

    assert decoder.status_code == 200
    assert decoder.mimetype == "text/javascript"
    assert b"ZXing" in decoder.data
    assert licence.status_code == 200
    assert b"Apache License" in licence.data


def test_about_credits_the_decoder(client):
    html = client.get("/about").get_data(as_text=True)

    assert re.search(
        r'ZXing</a>, <a href="/static/zxing/LICENSE">Apache License 2.0</a>', html
    )
