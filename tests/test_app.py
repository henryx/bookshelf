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


def test_login_limits_default(monkeypatch):
    monkeypatch.delenv("APP_LOGIN_MAX_FAILURES", raising=False)
    monkeypatch.delenv("APP_LOGIN_BLOCK_MINUTES", raising=False)

    app = create_app()

    assert app.config["LOGIN_MAX_FAILURES"] == 5
    assert app.config["LOGIN_BLOCK_MINUTES"] == 15


def test_login_limits_from_env(monkeypatch):
    monkeypatch.setenv("APP_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("APP_LOGIN_BLOCK_MINUTES", "30")

    app = create_app()

    assert app.config["LOGIN_MAX_FAILURES"] == 3
    assert app.config["LOGIN_BLOCK_MINUTES"] == 30


@pytest.mark.parametrize("name", ["APP_LOGIN_MAX_FAILURES", "APP_LOGIN_BLOCK_MINUTES"])
@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_invalid_login_limit_fails(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        create_app()


def test_overrides_are_applied():
    app = create_app({"TESTING": True, "SECRET_KEY": "override"})

    assert app.testing
    assert app.config["SECRET_KEY"] == "override"


def test_cross_origin_requests_are_not_allowed(client):
    rv = client.get("/login", headers={"Origin": "https://evil.example.com"})

    assert "Access-Control-Allow-Origin" not in rv.headers
