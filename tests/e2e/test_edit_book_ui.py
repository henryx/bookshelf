"""Editing a book in a browser: its title opens the edit dialog over the list."""

from uuid import UUID

import pytest
from flask import Flask
from playwright.sync_api import Page, expect

from bookshelf.db import get_db
from bookshelf.models.books import NewBook, add_book
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
