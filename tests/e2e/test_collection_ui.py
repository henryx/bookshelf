"""The collection in a browser: adding from the catalogue, the duplicate-ISBN
offer, changing copies in the book dialog and removing books on the collection page."""

from uuid import UUID

import pytest
from flask import Flask
from playwright.sync_api import Page, expect

from bookshelf.db import get_session
from bookshelf.models.books import NewBook, add_book
from bookshelf.models.users import User, create_user

EMMA_ISBN = "9780141439587"


@pytest.fixture
def emma(db_app: Flask, password: str) -> UUID:
    """A catalogue book in another user's collection only."""
    with db_app.app_context():
        session = get_session()
        other = create_user(session, "other@example.com", password)
        return add_book(session, other.id, NewBook("Emma", isbn=EMMA_ISBN))


@pytest.fixture
def dune(db_app: Flask, user: User) -> UUID:
    """A catalogue book in the reader's collection, with 2 copies."""
    with db_app.app_context():
        return add_book(get_session(), user.id, NewBook("Dune"), copies=2)


def test_add_from_the_catalogue_updates_the_row_in_place(reader_page: Page, emma):
    url = reader_page.url
    reader_page.get_by_label("Search books").fill("emm")
    expect(reader_page.locator(".book-title")).to_have_text(["Emma"])

    reader_page.get_by_role("button", name="Add Emma to your collection").click()

    expect(reader_page.locator("#book-list")).to_contain_text(
        "“Emma” added to your collection."
    )
    expect(reader_page.locator(".in-collection")).to_have_text("In your collection · 1")
    expect(reader_page.locator(".book-title")).to_have_text(["Emma"])  # search kept
    assert reader_page.url.startswith(url.rstrip("/"))


def test_duplicate_isbn_offers_the_existing_book(reader_page: Page, emma):
    reader_page.get_by_role("link", name="Add book").click()
    dialog = reader_page.locator("#add-book-dialog")
    dialog.get_by_role("combobox", name="Title").fill("Emma")
    dialog.get_by_label("ISBN").fill(EMMA_ISBN)
    dialog.get_by_label("Copies you own").fill("3")
    dialog.get_by_role("button", name="Add book").click()

    dialog.get_by_role("button", name="Add “Emma” to your collection").click()

    expect(reader_page.locator(".flash")).to_have_text(
        "“Emma” added to your collection."
    )
    expect(reader_page.locator(".in-collection")).to_have_text("In your collection · 3")


def test_change_copies_in_the_book_dialog(reader_page: Page, live_server: str, dune):
    reader_page.goto(f"{live_server}/")
    expect(reader_page.locator(".book-copies")).to_have_text("Copies · 2")

    reader_page.get_by_role("link", name="Dune").click()
    dialog = reader_page.locator("#book-dialog")
    dialog.get_by_label("Copies you own").fill("5")
    dialog.get_by_role("button", name="Save").click()

    expect(reader_page.locator(".flash")).to_have_text("Book “Dune” saved.")
    expect(reader_page.locator(".book-copies")).to_have_text("Copies · 5")


def test_remove_on_the_collection_page(reader_page: Page, live_server: str, dune):
    reader_page.goto(f"{live_server}/")

    reader_page.get_by_role("button", name="Remove Dune from your collection").click()

    expect(reader_page.locator("#book-list")).to_contain_text(
        "“Dune” removed from your collection."
    )
    expect(reader_page.locator(".book-title")).to_have_count(0)
    expect(reader_page.locator("#book-list")).to_contain_text(
        "Your collection is empty"
    )


def test_tick_read_on_the_collection_page(reader_page: Page, live_server: str, dune):
    reader_page.goto(f"{live_server}/")
    checkbox = reader_page.get_by_label("Dune read")
    expect(checkbox).not_to_be_checked()

    checkbox.check()

    expect(reader_page.locator("#book-list")).to_contain_text("“Dune” marked as read.")
    expect(reader_page.get_by_label("Dune read")).to_be_checked()
    # No visible Save button with JavaScript: it is inside <noscript>.
    expect(reader_page.locator(".read-form button")).to_be_hidden()
    reader_page.reload()
    expect(reader_page.get_by_label("Dune read")).to_be_checked()


def test_csv_import_shows_its_report_on_the_account_page(
    reader_page: Page, live_server: str, dune
):
    reader_page.goto(f"{live_server}/account")
    reader_page.get_by_label("Import books from CSV").set_input_files(
        files=[
            {
                "name": "books.csv",
                "mimeType": "text/csv",
                "buffer": b"title,copies\nDune,1\nEmma,2\n,3\n",
            }
        ]
    )
    url = reader_page.url

    reader_page.get_by_role("button", name="Import", exact=True).click()

    csv_section = reader_page.locator("#collection-csv")
    expect(csv_section).to_contain_text(
        "1 book added to your collection (1 new to the catalogue); "
        "1 already in your collection, skipped."
    )
    expect(csv_section).to_contain_text("Row 4: the title is required.")
    assert reader_page.url == url
    reader_page.goto(f"{live_server}/")
    expect(reader_page.locator(".book-title")).to_have_text(["Dune", "Emma"])


def test_csv_export_downloads_the_collection(reader_page: Page, live_server: str, dune):
    reader_page.goto(f"{live_server}/account")

    with reader_page.expect_download() as download:
        reader_page.get_by_role("link", name="Export CSV").click()

    assert download.value.suggested_filename.startswith("bookshelf-collection-")
    text = download.value.path().read_text(encoding="utf-8-sig")
    assert text.splitlines()[1].startswith("Dune,")
