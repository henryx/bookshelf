"""The about dialog: the app's version, licence, source code and credits."""

import re
from importlib.metadata import version

from flask.testing import FlaskClient

from bookshelf.models.users import User


def _log_in(client: FlaskClient, user: User, password: str) -> FlaskClient:
    rv = client.post("/login", data={"email": user.email, "password": password})
    assert rv.status_code == 302
    return client


def test_about_page_requires_login(db_client: FlaskClient):
    rv = db_client.get("/about")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_about_page_shows_version_licence_source_and_credits(
    db_client: FlaskClient, user: User, password: str
):
    """Without JavaScript the menu's About link opens this page."""
    html = _log_in(db_client, user, password).get("/about").get_data(as_text=True)

    assert "<h2>About Bookshelf</h2>" in html
    assert f'<span class="about-version">{version("bookshelf")}</span>' in html
    assert "Apache License 2.0" in html
    assert 'href="https://github.com/henryx/bookshelf"' in html
    for credit in ("Fraunces", "Bricolage Grotesque", "JetBrains Mono", "htmx"):
        assert credit in html
    for licence in ("fraunces", "bricolagegrotesque", "jetbrainsmono"):
        assert f'href="/static/fonts/OFL-{licence}.txt"' in html


def test_account_menu_opens_the_about_dialog(
    db_client: FlaskClient, user: User, password: str
):
    html = _log_in(db_client, user, password).get("/").get_data(as_text=True)

    assert re.search(
        r'<a href="/about" data-dialog-open="about-dialog">About</a>', html
    )
    dialog = re.search(r'<dialog id="about-dialog".*?</dialog>', html, re.DOTALL)
    assert dialog
    assert 'aria-labelledby="about-dialog-title"' in dialog[0]
    assert f'<span class="about-version">{version("bookshelf")}</span>' in dialog[0]
    assert "data-dialog-close" in dialog[0]
