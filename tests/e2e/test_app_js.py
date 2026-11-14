"""The behaviour app.js adds in a browser: error banner, dialogs, author rows,
suggestions and the account menu."""

import pytest
from flask import Flask
from playwright.sync_api import Page, Route, expect

from bookshelf.db import get_db
from bookshelf.models.books import Author, NewBook, add_book
from bookshelf.models.users import User

UNREACHABLE = "Could not reach the server"


@pytest.fixture
def herbert_book(db_app: Flask, user: User) -> None:
    """A book by Frank Herbert from Penguin, for the suggestions."""
    with db_app.app_context():
        book = NewBook("Dune", (Author("Frank", "Herbert"),), publisher="Penguin")
        add_book(get_db(), user.id, book)


def _open_add_book(page: Page) -> None:
    page.get_by_role("link", name="Add book").click()
    expect(page.locator("#add-book-dialog")).to_be_visible()


def _show_unreachable_error(page: Page) -> None:
    """Submit the add-book form while the server cannot be reached."""

    def abort(route: Route) -> None:
        route.abort()

    page.route("**/books/new", abort)
    _open_add_book(page)
    page.get_by_role("textbox", name="Title").fill("Dune")
    page.get_by_role("button", name="Add book").click()
    expect(page.locator("#error-banner")).to_contain_text(UNREACHABLE)
    page.unroute("**/books/new", abort)


# Error banner


def test_unreachable_server_shows_error_and_closes_dialog(reader_page: Page):
    _show_unreachable_error(reader_page)

    expect(reader_page.locator("#add-book-dialog")).to_be_hidden()


def test_new_request_clears_the_error(reader_page: Page):
    _show_unreachable_error(reader_page)

    reader_page.get_by_label("Search books").fill("dune")

    expect(reader_page.locator("#error-banner")).to_be_empty()


def test_publisher_suggestions_keep_the_error(reader_page: Page, herbert_book):
    _show_unreachable_error(reader_page)
    _open_add_book(reader_page)

    with reader_page.expect_response("**/publishers/suggestions*"):
        reader_page.get_by_label("Publisher", exact=True).fill("Pen")

    expect(reader_page.locator("#error-banner")).to_contain_text(UNREACHABLE)


def test_author_suggestions_keep_the_error(reader_page: Page, herbert_book):
    _show_unreachable_error(reader_page)
    _open_add_book(reader_page)

    with reader_page.expect_response("**/authors/suggestions*"):
        reader_page.locator("#book-authors-0-surname").fill("Her")

    expect(reader_page.locator("#error-banner")).to_contain_text(UNREACHABLE)


# Dialogs


def test_add_button_opens_dialog_without_leaving_the_page(reader_page: Page):
    url = reader_page.url

    _open_add_book(reader_page)

    assert reader_page.url == url


def test_close_button_closes_dialog(reader_page: Page):
    _open_add_book(reader_page)

    reader_page.locator("#add-book-dialog").get_by_role("button", name="Close").click()

    expect(reader_page.locator("#add-book-dialog")).to_be_hidden()


def test_content_swapped_into_closed_dialog_opens_it(
    admin_page: Page, live_server: str, user: User
):
    admin_page.goto(f"{live_server}/admin/users")

    admin_page.get_by_role("link", name=user.email).click()

    dialog = admin_page.locator("#user-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text(user.email)


# Author rows


def test_plus_adds_an_author_row_and_focuses_it(reader_page: Page):
    _open_add_book(reader_page)

    reader_page.get_by_role("button", name="Add another author").click()

    rows = reader_page.locator("#add-book-dialog [data-row]")
    expect(rows).to_have_count(2)
    expect(rows.nth(1).locator('[data-author-field="name"]')).to_be_focused()


def test_minus_removes_its_author_row(reader_page: Page):
    _open_add_book(reader_page)
    reader_page.get_by_role("button", name="Add another author").click()

    reader_page.get_by_role("button", name="Remove this author").click()

    expect(reader_page.locator("#add-book-dialog [data-row]")).to_have_count(1)


def test_added_rows_get_their_own_index(reader_page: Page):
    _open_add_book(reader_page)
    add = reader_page.get_by_role("button", name="Add another author")
    add.click()
    add.click()
    reader_page.get_by_role("button", name="Remove this author").first.click()

    add.click()

    names = reader_page.locator(
        '#add-book-dialog [data-row] [data-author-field="name"]'
    )
    ids = names.evaluate_all("inputs => inputs.map(i => i.name)")
    assert ids == ["authors-0-name", "authors-2-name", "authors-3-name"]


# Suggestions


def test_publisher_field_suggests_matching_publishers(reader_page: Page, herbert_book):
    _open_add_book(reader_page)

    reader_page.get_by_label("Publisher", exact=True).fill("pen")

    options = reader_page.locator("#book-publisher-suggestions option")
    expect(options).to_have_count(1)
    expect(options).to_have_attribute("value", "Penguin")


def test_added_author_row_suggests_authors(reader_page: Page, herbert_book):
    _open_add_book(reader_page)
    reader_page.get_by_role("button", name="Add another author").click()

    reader_page.locator("#book-authors-1-surname").fill("her")

    expect(
        reader_page.locator("#book-authors-1-surname-suggestions option")
    ).to_have_attribute("value", "Frank Herbert")


def test_picking_an_author_fills_name_and_surname(reader_page: Page, herbert_book):
    _open_add_book(reader_page)
    name = reader_page.locator("#book-authors-0-name")
    name.fill("Fra")
    expect(
        reader_page.locator("#book-authors-0-name-suggestions option")
    ).to_have_count(1)

    name.fill("Frank Herbert")  # what picking the suggestion puts in the field

    expect(name).to_have_value("Frank")
    expect(reader_page.locator("#book-authors-0-surname")).to_have_value("Herbert")


# Account menu


def test_click_outside_closes_account_menu(reader_page: Page):
    menu = reader_page.locator("details.avatar")
    menu.locator("summary").click()
    expect(menu).to_have_attribute("open", "")

    reader_page.locator("main").click()

    expect(menu).not_to_have_attribute("open", "")


def test_escape_closes_account_menu_and_focuses_it(reader_page: Page):
    menu = reader_page.locator("details.avatar")
    menu.locator("summary").click()

    reader_page.keyboard.press("Escape")

    expect(menu).not_to_have_attribute("open", "")
    expect(menu.locator("summary")).to_be_focused()
