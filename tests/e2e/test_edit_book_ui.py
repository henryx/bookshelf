"""Editing a book in a browser: its title opens the edit dialog over the list."""

from uuid import UUID

import pytest
from flask import Flask
from playwright.sync_api import Dialog, Page, expect

from bookshelf.db import get_db
from bookshelf.models.books import Author, NewBook, add_book, remove_from_collection
from bookshelf.models.users import User


@pytest.fixture
def dune(db_app: Flask, user: User) -> UUID:
    with db_app.app_context():
        return add_book(get_db(), user.id, NewBook("Dune", volume="1"))


def test_save_closes_the_dialog_and_confirms(reader_page: Page, dune):
    reader_page.get_by_label("Search books").fill("dun")
    expect(reader_page.locator(".book-title")).to_have_text(["Dune"])

    reader_page.get_by_role("link", name="Dune").click()
    dialog = reader_page.locator("#book-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator(".book-details-title")).to_have_text("Dune")
    dialog.get_by_label("Volume").fill("2")
    dialog.get_by_role("button", name="Save").click()

    expect(reader_page.locator(".flash")).to_have_text("Book “Dune” saved.")
    expect(dialog).to_be_hidden()
    assert "q=dun" in reader_page.url  # the list as it was


def test_errors_keep_the_dialog_open(dune, reader_page: Page):
    # dune first: the book exists before the page loads.
    reader_page.get_by_role("link", name="Dune").click()
    dialog = reader_page.locator("#book-dialog")
    dialog.get_by_label("ISBN").fill("12345")
    dialog.get_by_role("button", name="Save").click()

    expect(dialog.get_by_text("Not a valid ISBN.")).to_be_visible()
    expect(dialog).to_be_visible()


def test_title_opens_the_dialog_from_the_collection(
    reader_page: Page, live_server: str, dune
):
    reader_page.goto(f"{live_server}/collection")

    reader_page.get_by_role("link", name="Dune").click()

    expect(reader_page.locator("#book-dialog")).to_be_visible()


def test_fix_a_misspelled_author(db_app: Flask, user: User, reader_page: Page):
    with db_app.app_context():
        add_book(
            get_db(), user.id, NewBook("Emma", authors=(Author("Jnae", "Austen"),))
        )
    reader_page.reload()

    reader_page.get_by_role("link", name="Emma").click()
    dialog = reader_page.locator("#book-dialog")
    dialog.locator("#edit-book-authors-0-name").fill("Jane")
    dialog.get_by_role("button", name="Add another author").click()
    expect(dialog.locator("[data-row]")).to_have_count(2)
    dialog.get_by_label("Year").fill("1815")
    dialog.get_by_role("button", name="Save").click()

    expect(reader_page.locator(".flash")).to_have_text("Book “Emma” saved.")
    expect(reader_page.locator(".book-authors")).to_have_text("Jane Austen")
    expect(reader_page.locator(".book-year")).to_have_text("1815")


def test_admin_deletes_a_book_after_confirming(
    db_app: Flask, user: User, admin_page: Page
):
    with db_app.app_context():
        conn = get_db()
        emma = add_book(conn, user.id, NewBook("Emma"))
        remove_from_collection(conn, user.id, emma)  # in nobody's collection
    admin_page.reload()
    messages: list[str] = []

    def accept(dialog: Dialog) -> None:
        messages.append(dialog.message)
        dialog.accept()

    admin_page.on("dialog", accept)
    admin_page.get_by_role("link", name="Emma").click()
    admin_page.locator("#book-dialog").get_by_role("button", name="Delete book").click()

    expect(admin_page.locator(".flash")).to_have_text("Book “Emma” deleted.")
    expect(admin_page.locator(".book-title")).to_have_count(0)
    assert messages == ["Delete “Emma”? This cannot be undone."]
