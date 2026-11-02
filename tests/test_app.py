import pytest

from bookshelf import create_app


def test_secret_key_from_env(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "s3cret")

    assert create_app().config["SECRET_KEY"] == "s3cret"


@pytest.mark.parametrize("value", [None, ""])
def test_missing_secret_key_fails(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("APP_SECRET_KEY", value)

    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        create_app()


def test_overrides_are_applied():
    app = create_app({"TESTING": True, "SECRET_KEY": "override"})

    assert app.testing
    assert app.config["SECRET_KEY"] == "override"


def test_cross_origin_requests_are_not_allowed(client):
    rv = client.get("/login", headers={"Origin": "https://evil.example.com"})

    assert "Access-Control-Allow-Origin" not in rv.headers
