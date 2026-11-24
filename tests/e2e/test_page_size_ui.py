"""The page size selector of the book lists and the authors list in a browser."""

import re

from flask import Flask
from playwright.sync_api import Page, expect

from bookshelf.db import get_session
from bookshelf.models.books import Author, NewBook, add_book
from bookshelf.models.users import User


def _add_books(db_app: Flask, user: User, count: int) -> None:
    with db_app.app_context():
        session = get_session()
        for n in range(count):
            add_book(
                session,
                user.id,
                NewBook(f"Book {n:02}", (Author("", f"Surname{n:02}"),)),
            )


def test_changing_the_page_size_reloads_the_list(
    db_app: Flask, user: User, reader_page: Page, live_server: str
):
    _add_books(db_app, user, 12)
    reader_page.goto(f"{live_server}/catalogue?page=2")
    titles = reader_page.locator("#book-list a.book-title")
    expect(titles).to_have_count(2)

    reader_page.select_option("#book-search-size", "50")

    # From the first page, with every book, and the address keeps the size.
    expect(titles).to_have_count(12)
    expect(reader_page).to_have_url(re.compile(r"/catalogue\?.*size=50"))
    expect(reader_page.locator("#book-list nav.pages")).to_have_count(0)


def test_changing_the_authors_page_size_reloads_the_list(
    db_app: Flask, user: User, reader_page: Page, live_server: str
):
    _add_books(db_app, user, 12)
    reader_page.goto(f"{live_server}/authors/")
    names = reader_page.locator("#author-list a.author-name")
    expect(names).to_have_count(10)

    reader_page.select_option("#author-search-size", "50")

    expect(names).to_have_count(12)
