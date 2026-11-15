"""The authors page in a browser: renaming an author for every book."""

import pytest
from flask import Flask
from playwright.sync_api import Page, expect

from bookshelf.db import get_db
from bookshelf.models.books import Author, NewBook, add_book
from bookshelf.models.users import User


@pytest.fixture
def misspelled(db_app: Flask, user: User) -> None:
    with db_app.app_context():
        conn = get_db()
        add_book(conn, user.id, NewBook("Dune", authors=(Author("Frnak", "Herbert"),)))
        add_book(conn, user.id, NewBook("Emma", authors=(Author("Jane", "Austen"),)))


def test_rename_an_author_in_the_dialog(
    misspelled, reader_page: Page, live_server: str
):
    reader_page.locator("details.avatar summary").click()
    reader_page.get_by_role("link", name="Authors").click()
    reader_page.get_by_label("Search authors").fill("herb")
    expect(reader_page.locator(".author-name")).to_have_text(["Herbert, Frnak"])

    reader_page.get_by_role("link", name="Rename Frnak Herbert").click()
    dialog = reader_page.locator("#author-dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_label("Name", exact=True).fill("Frank")
    dialog.get_by_role("button", name="Rename").click()

    expect(reader_page.locator(".flash")).to_have_text(
        "“Frnak Herbert” renamed to “Frank Herbert”."
    )
    expect(reader_page.locator(".author-name")).to_have_text(["Herbert, Frank"])
    reader_page.goto(live_server)
    expect(reader_page.locator(".book-authors")).to_have_text(
        ["Frank Herbert", "Jane Austen"]
    )


def test_an_authors_books_keep_to_the_author(
    misspelled, reader_page: Page, live_server: str
):
    reader_page.goto(f"{live_server}/authors")

    reader_page.get_by_role("link", name="Herbert, Frnak").click()

    expect(
        reader_page.get_by_role("heading", name="Books by Frnak Herbert")
    ).to_be_visible()
    expect(reader_page.locator(".book-title")).to_have_text(["Dune"])
    reader_page.get_by_label("Search books").fill("e")  # Dune and Emma both match
    expect(reader_page.locator(".book-count")).to_have_text("1 book")
    expect(reader_page.locator(".book-title")).to_have_text(["Dune"])
    assert "author=" in reader_page.url


def test_an_author_under_a_book_opens_their_books(misspelled, reader_page: Page):
    reader_page.locator(".book-authors").get_by_role("link", name="Jane Austen").click()

    expect(
        reader_page.get_by_role("heading", name="Books by Jane Austen")
    ).to_be_visible()
    expect(reader_page.locator(".book-title")).to_have_text(["Emma"])
