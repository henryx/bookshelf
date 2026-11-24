"""Picking a catalogue book from the add-book form's title or ISBN suggestions."""

import pytest
from flask import Flask
from playwright.sync_api import Locator, Page, expect

from bookshelf.db import get_session
from bookshelf.models.books import Author, NewBook, add_book
from bookshelf.models.users import create_user

DUNE_ISBN = "9780441172719"


@pytest.fixture
def dune(db_app: Flask, password: str) -> None:
    """A catalogue book in another user's collection only."""
    with db_app.app_context():
        session = get_session()
        other = create_user(session, "other@example.com", password)
        book = NewBook(
            "Dune", (Author("Frank", "Herbert"),), isbn=DUNE_ISBN, publisher="Chilton"
        )
        add_book(session, other.id, book)


def _open_add_book(page: Page) -> Locator:
    page.get_by_role("link", name="Add book").click()
    dialog = page.locator("#add-book-dialog")
    expect(dialog).to_be_visible()
    return dialog


def _pick(field: Locator, typed: str, value: str) -> None:
    """Type `typed`, wait for the suggestions, then click the one with `value`."""
    field.fill(typed)
    listbox = field.page.locator(f"#{field.get_attribute('aria-controls')}")
    listbox.locator(f'[role="option"][data-value="{value}"]').click()


@pytest.mark.parametrize(
    ("label", "typed", "value"),
    [
        ("Title", "du", "Dune"),
        ("ISBN", "978044", DUNE_ISBN),
    ],
)
def test_picking_a_suggestion_fills_the_form_read_only(
    reader_page: Page, dune, label, typed, value
):
    dialog = _open_add_book(reader_page)

    _pick(dialog.get_by_role("combobox", name=label), typed, value)

    expect(dialog.get_by_role("button", name="Add to my collection")).to_be_visible()
    for field, expected in [
        ("#book-title", "Dune"),
        ("#book-isbn", DUNE_ISBN),
        ("#book-authors-0-surname", "Herbert"),
        ("#book-publisher", "Chilton"),
    ]:
        expect(dialog.locator(field)).to_have_value(expected)
        expect(dialog.locator(field)).not_to_be_editable()
    expect(dialog.get_by_label("Copies you own")).to_be_editable()


def test_adding_the_picked_book_to_the_collection(reader_page: Page, dune):
    dialog = _open_add_book(reader_page)
    _pick(dialog.get_by_role("combobox", name="Title"), "du", "Dune")
    dialog.get_by_label("Copies you own").fill("2")

    dialog.get_by_role("button", name="Add to my collection").click()

    expect(reader_page.locator(".flash")).to_have_text(
        "“Dune” added to your collection."
    )
    expect(reader_page.locator(".book-title")).to_have_text(["Dune"])


def test_choosing_another_book_empties_the_form(reader_page: Page, dune):
    dialog = _open_add_book(reader_page)
    _pick(dialog.get_by_role("combobox", name="Title"), "du", "Dune")

    dialog.get_by_role("link", name="Choose another book").click()

    title = dialog.get_by_role("combobox", name="Title")
    expect(title).to_have_value("")
    expect(title).to_be_editable()
    expect(dialog.get_by_role("button", name="Add book")).to_be_visible()


def test_typing_a_whole_title_does_not_pick_it(reader_page: Page, dune):
    dialog = _open_add_book(reader_page)
    title = dialog.get_by_role("combobox", name="Title")
    title.fill("Du")
    expect(dialog.locator('#book-title-suggestions [role="option"]')).to_have_count(1)

    title.press_sequentially("ne")

    expect(dialog.locator('#book-title-suggestions [role="option"]')).to_have_count(1)
    expect(title).to_be_editable()
    expect(dialog.get_by_role("button", name="Add book")).to_be_visible()


@pytest.mark.parametrize("close", ["button", "escape"])
def test_closing_the_dialog_empties_a_picked_book(reader_page: Page, dune, close):
    dialog = _open_add_book(reader_page)
    _pick(dialog.get_by_role("combobox", name="Title"), "du", "Dune")
    expect(dialog.get_by_role("button", name="Add to my collection")).to_be_visible()

    if close == "button":
        dialog.get_by_role("button", name="Close").click()
    else:
        reader_page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    dialog = _open_add_book(reader_page)

    title = dialog.get_by_role("combobox", name="Title")
    expect(title).to_have_value("")
    expect(title).to_be_editable()
    expect(dialog.get_by_role("button", name="Add book")).to_be_visible()
    # Picking still works in the emptied form.
    _pick(dialog.get_by_role("combobox", name="ISBN"), "978044", DUNE_ISBN)
    expect(dialog.get_by_role("button", name="Add to my collection")).to_be_visible()
