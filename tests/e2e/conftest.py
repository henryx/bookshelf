"""
Browser tests of the scripts in static/js/: the app is served on a free local port by a
werkzeug server in a thread, and driven with Playwright: Chromium (tox -e e2e) or
Firefox (tox -e e2e-firefox).
"""

from collections.abc import Iterator
from threading import Thread

import pytest
from flask import Flask
from playwright.sync_api import Page, expect
from werkzeug.serving import make_server

from bookshelf.db import get_session
from bookshelf.models.users import Role, User, create_user


@pytest.fixture
def live_server(db_app: Flask) -> Iterator[str]:
    """The app on an empty database, served over HTTP; yields its base URL."""
    server = make_server("127.0.0.1", 0, db_app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


def _log_in(page: Page, base_url: str, user: User, password: str) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Email").fill(user.email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()
    # Logging in lands on the collection; the fixtures start from the catalogue.
    expect(page).to_have_url(f"{base_url}/")
    page.goto(f"{base_url}/catalogue")
    expect(page.locator(".book-search")).to_be_visible()


@pytest.fixture
def reader_page(page: Page, live_server: str, user: User, password: str) -> Page:
    """The main page, logged in as a regular user."""
    _log_in(page, live_server, user, password)
    return page


@pytest.fixture
def admin(db_app: Flask, password: str) -> User:
    with db_app.app_context():
        return create_user(
            get_session(), "admin@example.com", password, role=Role.ADMIN
        )


@pytest.fixture
def admin_page(page: Page, live_server: str, admin: User, password: str) -> Page:
    """The main page, logged in as an admin."""
    _log_in(page, live_server, admin, password)
    return page
