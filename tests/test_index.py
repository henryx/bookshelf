import json


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
