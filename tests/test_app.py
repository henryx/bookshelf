import pytest
from flask import request

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
    monkeypatch.delenv("APP_LOGIN_MAX_IP_FAILURES", raising=False)

    app = create_app()

    assert app.config["LOGIN_MAX_FAILURES"] == 5
    assert app.config["LOGIN_BLOCK_MINUTES"] == 15
    assert app.config["LOGIN_MAX_IP_FAILURES"] == 20


def test_login_limits_from_env(monkeypatch):
    monkeypatch.setenv("APP_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("APP_LOGIN_BLOCK_MINUTES", "30")
    monkeypatch.setenv("APP_LOGIN_MAX_IP_FAILURES", "50")

    app = create_app()

    assert app.config["LOGIN_MAX_FAILURES"] == 3
    assert app.config["LOGIN_BLOCK_MINUTES"] == 30
    assert app.config["LOGIN_MAX_IP_FAILURES"] == 50


@pytest.mark.parametrize(
    "name",
    ["APP_LOGIN_MAX_FAILURES", "APP_LOGIN_BLOCK_MINUTES", "APP_LOGIN_MAX_IP_FAILURES"],
)
@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_invalid_login_limit_fails(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        create_app()


def test_db_pool_size_default(monkeypatch):
    monkeypatch.delenv("APP_DB_POOL_SIZE", raising=False)

    assert create_app().config["DATABASE_POOL_SIZE"] == 5


def test_db_pool_size_from_env(monkeypatch):
    monkeypatch.setenv("APP_DB_POOL_SIZE", "2")

    assert create_app().config["DATABASE_POOL_SIZE"] == 2


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_invalid_db_pool_size_fails(monkeypatch, value):
    monkeypatch.setenv("APP_DB_POOL_SIZE", value)

    with pytest.raises(RuntimeError, match="APP_DB_POOL_SIZE"):
        create_app()


def test_overrides_are_applied():
    app = create_app({"TESTING": True, "SECRET_KEY": "override"})

    assert app.testing
    assert app.config["SECRET_KEY"] == "override"


def test_cross_origin_requests_are_not_allowed(client):
    rv = client.get("/login", headers={"Origin": "https://evil.example.com"})

    assert "Access-Control-Allow-Origin" not in rv.headers


FORWARDED = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}


def _client_seen_by(app):
    @app.route("/_whoami")
    def whoami():
        return f"{request.remote_addr} {request.scheme}"

    return app.test_client().get("/_whoami", headers=FORWARDED).text


def test_forwarded_headers_ignored_by_default(monkeypatch):
    monkeypatch.delenv("APP_TRUSTED_PROXIES", raising=False)

    assert _client_seen_by(create_app()) == "127.0.0.1 http"


def test_forwarded_headers_trusted_behind_proxy(monkeypatch):
    monkeypatch.setenv("APP_TRUSTED_PROXIES", "1")

    assert _client_seen_by(create_app()) == "203.0.113.7 https"


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_invalid_trusted_proxies_fails(monkeypatch, value):
    monkeypatch.setenv("APP_TRUSTED_PROXIES", value)

    with pytest.raises(RuntimeError, match="APP_TRUSTED_PROXIES"):
        create_app()


def test_min_password_length_default(monkeypatch):
    monkeypatch.delenv("APP_MIN_PASSWORD_LENGTH", raising=False)

    assert create_app().config["MIN_PASSWORD_LENGTH"] == 12


def test_min_password_length_from_env(monkeypatch):
    monkeypatch.setenv("APP_MIN_PASSWORD_LENGTH", "16")

    assert create_app().config["MIN_PASSWORD_LENGTH"] == 16


@pytest.mark.parametrize("value", ["11", "0", "-1", "abc"])
def test_min_password_length_below_12_fails(monkeypatch, value):
    monkeypatch.setenv("APP_MIN_PASSWORD_LENGTH", value)

    with pytest.raises(RuntimeError, match="APP_MIN_PASSWORD_LENGTH"):
        create_app()
