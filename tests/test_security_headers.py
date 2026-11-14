"""Security headers on every response: pages, partials, static files and errors."""

import pytest
from flask.testing import FlaskClient

HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def _csp(header: str) -> dict[str, str]:
    """The policy's directives, by name."""
    directives = (d.strip().split(" ", 1) for d in header.split(";") if d.strip())
    return {name: value for name, value in directives}


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/login", {}),  # a page
        ("/login", {"HX-Request": "true"}),  # an htmx partial
        ("/static/style.css", {}),  # a static file
        ("/no-such-page", {}),  # an error page
    ],
)
def test_every_response_has_security_headers(client: FlaskClient, path, headers):
    rv = client.get(path, headers=headers)

    for name, value in HEADERS.items():
        assert rv.headers.get(name) == value
    assert "Content-Security-Policy" in rv.headers


def test_csp_allows_only_the_apps_own_resources(client: FlaskClient):
    csp = _csp(client.get("/login").headers["Content-Security-Policy"])

    assert csp == {
        "default-src": "'self'",
        "base-uri": "'self'",
        "form-action": "'self'",
        "frame-ancestors": "'none'",
        "object-src": "'none'",
    }


def test_htmx_does_not_inject_inline_styles(client: FlaskClient):
    """The CSP allows no inline <style>: the indicator rules are in style.css."""
    html = client.get("/login").get_data(as_text=True)

    assert '"includeIndicatorStyles": false' in html
