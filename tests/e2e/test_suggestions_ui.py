"""The suggestion list under a field (js/suggestions.js): shown, moved through, picked from, closed.
The same in every browser: it is not a <datalist>, whose dropdown Firefox opens only for
the options there when a key is pressed."""

import pytest
from flask import Flask
from playwright.sync_api import Locator, Page, expect

from bookshelf.db import get_session
from bookshelf.models.books import Author, NewBook, add_book
from bookshelf.models.users import User, create_user


@pytest.fixture
def books(db_app: Flask, user: User, password: str) -> None:
    """Penguin's "101 storie zen", and two catalogue books called "Dune"."""
    with db_app.app_context():
        session = get_session()
        add_book(
            session,
            user.id,
            NewBook(
                "101 storie zen", (Author("Nyogen", "Senzaki"),), publisher="Penguin"
            ),
        )
        other = create_user(session, "other@example.com", password)
        for author, isbn in [
            (Author("Frank", "Herbert"), "9780441172719"),
            (Author("Brian", "Herbert"), "9780765312884"),
        ]:
            add_book(session, other.id, NewBook("Dune", (author,), isbn=isbn))


def _open_add_book(page: Page) -> Locator:
    page.get_by_role("link", name="Add book").click()
    dialog = page.locator("#add-book-dialog")
    expect(dialog).to_be_visible()
    return dialog


def _list(field: Locator) -> Locator:
    return field.page.locator(f"#{field.get_attribute('aria-controls')}")


def test_typing_quickly_shows_the_suggestions(books, reader_page: Page):
    dialog = _open_add_book(reader_page)
    title = dialog.get_by_role("combobox", name="Title")

    title.press_sequentially("101", delay=20)

    expect(_list(title)).to_be_visible()
    expect(_list(title).get_by_role("option")).to_have_count(1)
    expect(_list(title).get_by_role("option")).to_contain_text("101 storie zen")
    expect(title).to_have_attribute("aria-expanded", "true")


def test_arrow_down_and_enter_pick_without_submitting(books, reader_page: Page):
    dialog = _open_add_book(reader_page)
    publisher = dialog.get_by_label("Publisher", exact=True)
    publisher.fill("pen")
    expect(_list(publisher)).to_be_visible()

    publisher.press("ArrowDown")
    expect(_list(publisher).get_by_role("option")).to_have_attribute(
        "aria-selected", "true"
    )
    publisher.press("Enter")

    expect(publisher).to_have_value("Penguin")
    expect(_list(publisher)).to_be_hidden()
    expect(publisher).to_have_attribute("aria-expanded", "false")
    expect(dialog).to_be_visible()  # the form was not submitted


def test_escape_closes_the_list_then_the_dialog(books, reader_page: Page):
    dialog = _open_add_book(reader_page)
    publisher = dialog.get_by_label("Publisher", exact=True)
    publisher.fill("pen")
    expect(_list(publisher)).to_be_visible()

    publisher.press("Escape")
    expect(_list(publisher)).to_be_hidden()
    expect(dialog).to_be_visible()

    publisher.press("Escape")
    expect(dialog).to_be_hidden()


def test_leaving_the_field_closes_the_list(books, reader_page: Page):
    dialog = _open_add_book(reader_page)
    publisher = dialog.get_by_label("Publisher", exact=True)
    publisher.fill("pen")
    expect(_list(publisher)).to_be_visible()

    dialog.get_by_label("Series").focus()

    expect(_list(publisher)).to_be_hidden()


def test_a_title_two_books_share_picks_the_one_clicked(books, reader_page: Page):
    dialog = _open_add_book(reader_page)
    title = dialog.get_by_role("combobox", name="Title")
    title.fill("Dune")
    expect(_list(title).get_by_role("option")).to_have_count(2)

    _list(title).get_by_role("option").filter(has_text="Brian Herbert").click()

    expect(dialog.get_by_role("button", name="Add to my collection")).to_be_visible()
    expect(dialog.locator("#book-isbn")).to_have_value("9780765312884")
