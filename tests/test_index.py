import json

from flask import abort

from bookshelf import app


def test_index_route(client):
    rv = client.get("/")
    body = json.loads(rv.data)

    assert rv.status_code == 200
    assert body.get("result") == "ok"


def test_notfound_route(client):
    rv = client.get("/notfound")
    body = json.loads(rv.data)

    assert rv.status_code == 404
    assert body.get("result") == "ko"


def test_method_not_allowed_route(client):
    rv = client.post("/")
    body = json.loads(rv.data)

    assert rv.status_code == 405
    assert body.get("result") == "ko"


def _raise_runtime_error():
    raise RuntimeError("secret internals")


def _raise_bad_request():
    abort(400)


def test_internal_error_hides_traceback_and_logs(client, monkeypatch, caplog):
    monkeypatch.setitem(app.view_functions, "index_route.index", _raise_runtime_error)

    rv = client.get("/")
    body = json.loads(rv.data)

    assert rv.status_code == 500
    assert body.get("result") == "ko"
    assert "traceback" not in body
    assert "secret internals" not in rv.get_data(as_text=True)
    assert "secret internals" in caplog.text


def test_http_error_keeps_status_code(client, monkeypatch):
    monkeypatch.setitem(app.view_functions, "index_route.index", _raise_bad_request)

    rv = client.get("/")
    body = json.loads(rv.data)

    assert rv.status_code == 400
    assert body.get("result") == "ko"
