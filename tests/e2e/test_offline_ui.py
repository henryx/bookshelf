"""The service worker: the collection and catalogue last seen online are shown,
read-only, without a connection; other pages show the offline page; logging
out forgets the saved lists."""

from uuid import UUID

import pytest
from flask import Flask
from playwright.sync_api import Page, expect

from bookshelf.db import get_db
from bookshelf.models.books import NewBook, add_book
from bookshelf.models.users import User

# These fail in Firefox whatever app.js and sw.js do (they did before any change
# to them): there, the page Playwright takes offline (set_offline) is not served
# by the service worker's cache.
pytestmark = pytest.mark.skip_browser("firefox")


@pytest.fixture
def dune(db_app: Flask, user: User) -> UUID:
    """A book in the reader's collection."""
    with db_app.app_context():
        return add_book(get_db(), user.id, NewBook("Dune"))


def _visit_through_worker(page: Page, url: str) -> None:
    """Open url once the service worker controls the page, so it is saved."""
    page.goto(url)
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.reload()
    page.wait_for_function("async (url) => !!(await caches.match(url))", arg=url)


def test_collection_is_shown_read_only_offline(
    reader_page: Page, live_server: str, dune: UUID
):
    _visit_through_worker(reader_page, f"{live_server}/")

    reader_page.context.set_offline(True)
    reader_page.reload()

    expect(reader_page.locator(".book-title")).to_have_text(["Dune"])
    expect(reader_page.locator("#error-banner")).to_contain_text("You're offline")
    expect(reader_page.get_by_label("Dune read")).to_be_disabled()
    expect(reader_page.get_by_role("button", name="Remove Dune")).to_be_disabled()
    expect(reader_page.get_by_label("Search books")).to_be_disabled()


def test_page_never_saved_shows_the_offline_page(reader_page: Page, live_server: str):
    _visit_through_worker(reader_page, f"{live_server}/catalogue")

    reader_page.context.set_offline(True)
    reader_page.goto(f"{live_server}/authors")

    expect(reader_page.locator("h1")).to_have_text("You're offline")


def test_logging_out_forgets_the_saved_lists(
    reader_page: Page, live_server: str, dune: UUID
):
    _visit_through_worker(reader_page, f"{live_server}/")

    reader_page.locator("details.avatar summary").click()
    reader_page.get_by_role("button", name="Log out").click()
    expect(reader_page).to_have_url(f"{live_server}/login")
    reader_page.wait_for_function("async () => !(await caches.has('bookshelf-pages'))")

    reader_page.context.set_offline(True)
    reader_page.goto(f"{live_server}/")

    expect(reader_page.locator("h1")).to_have_text("You're offline")
    expect(reader_page.get_by_text("Dune")).to_have_count(0)
