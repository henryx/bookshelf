import re
from typing import NoReturn

import pytest
from flask import Flask, abort
from flask.testing import FlaskClient

from bookshelf import create_app

# Any GET-only route works; the view is replaced, so login is not involved.
ROUTE = "/catalogue"
ENDPOINT = "main.index"
HTMX = {"HX-Request": "true"}


def _raise_runtime_error() -> NoReturn:
    raise RuntimeError("secret internals")


def _raise_bad_request() -> NoReturn:
    abort(400)


def test_not_found_renders_error_page(client: FlaskClient):
    rv = client.get("/notfound")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 404
    assert rv.mimetype == "text/html"
    assert "<html" in html
    assert "Not Found" in html


def test_method_not_allowed_renders_error_page(client: FlaskClient):
    rv = client.post(ROUTE)

    assert rv.status_code == 405
    assert "Method Not Allowed" in rv.get_data(as_text=True)


def test_internal_error_hides_details_and_logs(
    app: Flask,
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setitem(app.view_functions, ENDPOINT, _raise_runtime_error)

    rv = client.get(ROUTE)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 500
    assert "Something went wrong" in html
    assert "secret internals" not in html
    assert "Traceback" not in html
    assert "secret internals" in caplog.text


def test_http_error_keeps_status_code(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(app.view_functions, ENDPOINT, _raise_bad_request)

    rv = client.get(ROUTE)

    assert rv.status_code == 400
    assert "Bad Request" in rv.get_data(as_text=True)


def test_htmx_error_is_retargeted_to_banner(client: FlaskClient):
    rv = client.get("/notfound", headers=HTMX)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 404
    assert rv.headers["HX-Retarget"] == "#error-banner"
    assert rv.headers["HX-Reswap"] == "innerHTML"
    assert "<html" not in html
    assert "Not Found" in html


def test_htmx_internal_error_hides_details(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(app.view_functions, ENDPOINT, _raise_runtime_error)

    rv = client.get(ROUTE, headers=HTMX)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 500
    assert rv.headers["HX-Retarget"] == "#error-banner"
    assert "Something went wrong" in html
    assert "secret internals" not in html


def test_htmx_csrf_failure_is_shown_to_user():
    client = create_app({"TESTING": True}).test_client()

    rv = client.post(
        "/login", data={"email": "a@example.com", "password": "pw"}, headers=HTMX
    )

    assert rv.status_code == 400
    assert rv.headers["HX-Retarget"] == "#error-banner"
    assert "CSRF" in rv.get_data(as_text=True)


def test_pages_have_error_banner_and_swap_errors(client: FlaskClient):
    html = client.get("/login").get_data(as_text=True)

    assert 'id="error-banner"' in html
    assert "responseHandling" in html
    # The scripts, each deferred, in this order: their listeners run in it.
    scripts = re.findall(r'<script src="/static/js/([a-z]+\.js)" defer></script>', html)
    assert scripts == [
        "errors.js",
        "dialogs.js",
        "rows.js",
        "suggestions.js",
        "menu.js",
        "scan.js",
        "offline.js",
    ]
