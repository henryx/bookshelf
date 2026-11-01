import json
from typing import NoReturn

import pytest
from flask import Flask, abort
from flask.testing import FlaskClient

# Any GET-only route works; the view is replaced, so login is not involved.
ROUTE = "/"
ENDPOINT = "main.index"


def _raise_runtime_error() -> NoReturn:
    raise RuntimeError("secret internals")


def _raise_bad_request() -> NoReturn:
    abort(400)


def test_not_found(client: FlaskClient):
    rv = client.get("/notfound")
    body = json.loads(rv.data)

    assert rv.status_code == 404
    assert body.get("result") == "ko"


def test_method_not_allowed(client: FlaskClient):
    rv = client.post(ROUTE)
    body = json.loads(rv.data)

    assert rv.status_code == 405
    assert body.get("result") == "ko"


def test_internal_error_hides_traceback_and_logs(
    app: Flask,
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setitem(app.view_functions, ENDPOINT, _raise_runtime_error)

    rv = client.get(ROUTE)
    body = json.loads(rv.data)

    assert rv.status_code == 500
    assert body.get("result") == "ko"
    assert "traceback" not in body
    assert "secret internals" not in rv.get_data(as_text=True)
    assert "secret internals" in caplog.text


def test_http_error_keeps_status_code(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(app.view_functions, ENDPOINT, _raise_bad_request)

    rv = client.get(ROUTE)
    body = json.loads(rv.data)

    assert rv.status_code == 400
    assert body.get("result") == "ko"
