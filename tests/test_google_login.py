"""Logging in with Google: users whose AUTH_METHOD is GOOGLE, bound on their first login."""

from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.integrations.base_client.errors import OAuthError
from flask import Flask
from flask.testing import FlaskClient

from bookshelf import create_app
from bookshelf.db import close_pool, get_session
from bookshelf.models.users import (
    AuthMethod,
    User,
    create_user,
    find_by_id,
    set_locked,
)
from bookshelf.routes.auth import (
    GOOGLE_LOGIN_FAILED,
    GOOGLE_LOGIN_REFUSED,
    google_client,
)

GOOGLE = {"GOOGLE_CLIENT_ID": "client-id", "GOOGLE_CLIENT_SECRET": "client-secret"}


@pytest.fixture
def google_app(db_app: Flask) -> Iterator[Flask]:
    """The app with Google login configured, on db_app's emptied database."""
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, **GOOGLE})
    yield app
    close_pool(app)


@pytest.fixture
def google_user(google_app: Flask) -> User:
    with google_app.app_context():
        return create_user(
            get_session(), "gina@example.com", None, auth_method=AuthMethod.GOOGLE
        )


type Answer = Callable[..., None]


@pytest.fixture
def google_answers(google_app: Flask, monkeypatch: pytest.MonkeyPatch) -> Answer:
    """
    Make Google's answer to the callback the given ID token claims (or an
    OAuthError): stands in for the code-for-token exchange with Google.
    """

    def answer(claims: dict[str, object] | None = None, error: str = "") -> None:
        def exchange() -> dict[str, object]:
            if error:
                raise OAuthError(error=error)
            return {"access_token": "token", "userinfo": claims}

        with google_app.app_context():
            monkeypatch.setattr(google_client(), "authorize_access_token", exchange)

    return answer


def _claims(email: str = "gina@example.com", **extra: object) -> dict[str, object]:
    return {"sub": "google-sub-1", "email": email, "email_verified": True, **extra}


def _logged_in(client: FlaskClient) -> bool:
    return client.get("/").status_code == 200


# The login page


def test_login_page_offers_google_when_configured(google_app: Flask):
    html = google_app.test_client().get("/login").get_data(as_text=True)

    assert 'href="/login/google"' in html
    assert "Log in with Google" in html


def test_login_page_without_google(db_client: FlaskClient):
    html = db_client.get("/login").get_data(as_text=True)

    assert "/login/google" not in html
    assert db_client.get("/login/google").status_code == 404
    assert db_client.get("/login/google/callback").status_code == 404


def test_google_login_starts_at_google(google_app: Flask):
    rv = google_app.test_client().get("/login/google")

    assert rv.status_code == 302
    location = urlsplit(rv.headers["Location"])
    assert f"{location.scheme}://{location.netloc}" == "https://accounts.google.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost/login/google/callback"]
    assert query["scope"] == ["openid email"]
    assert query["response_type"] == ["code"]
    # Checked on the way back: the callback must come from this login.
    assert query["state"] and query["nonce"]


# The callback


def test_a_google_user_logs_in_and_is_bound(
    google_app: Flask, google_user: User, google_answers: Answer
):
    google_answers(_claims())
    client = google_app.test_client()

    rv = client.get("/login/google/callback?code=c&state=s")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/"
    assert _logged_in(client)
    with google_app.app_context():
        bound = find_by_id(get_session(), google_user.id)
    assert bound is not None and bound.google_subject == "google-sub-1"


def test_login_is_logged(
    google_app: Flask, google_user: User, google_answers: Answer, caplog
):
    google_answers(_claims())

    google_app.test_client().get("/login/google/callback")

    assert "Google login for gina@example.com" in caplog.text


@pytest.mark.parametrize(
    "claims",
    [
        _claims(email_verified=False),  # Google has not checked the email
        _claims(email="reader@example.com"),  # a local user
        _claims(email="nobody@example.com"),  # no user
    ],
    ids=["unverified", "local-user", "unknown"],
)
def test_refused_google_logins(
    google_app: Flask,
    google_user: User,
    user: User,
    google_answers: Answer,
    claims: dict[str, object],
    caplog,
):
    google_answers(claims)
    client = google_app.test_client()

    rv = client.get("/login/google/callback", follow_redirects=True)

    assert GOOGLE_LOGIN_REFUSED in rv.get_data(as_text=True)
    assert not _logged_in(client)
    assert f"Google login refused for {claims['email']}" in caplog.text


def test_a_locked_google_user_is_refused(
    google_app: Flask, google_user: User, google_answers: Answer
):
    with google_app.app_context():
        set_locked(get_session(), google_user, locked=True)
    google_answers(_claims())
    client = google_app.test_client()

    rv = client.get("/login/google/callback", follow_redirects=True)

    assert GOOGLE_LOGIN_REFUSED in rv.get_data(as_text=True)
    assert not _logged_in(client)


def test_a_failed_google_answer_says_so(
    google_app: Flask, google_user: User, google_answers: Answer, caplog
):
    """E.g. the user cancelled at Google, or the state does not match."""
    google_answers(error="access_denied")
    client = google_app.test_client()

    rv = client.get("/login/google/callback", follow_redirects=True)

    assert GOOGLE_LOGIN_FAILED in rv.get_data(as_text=True)
    assert not _logged_in(client)
    assert "access_denied" in caplog.text


def test_google_users_cannot_log_in_with_a_password(
    google_app: Flask, google_user: User
):
    rv = google_app.test_client().post(
        "/login", data={"email": google_user.email, "password": "anything at all"}
    )

    assert "Invalid email or password" in rv.get_data(as_text=True)


def test_half_the_google_settings_stop_the_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.delenv("APP_GOOGLE_CLIENT_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="APP_GOOGLE_CLIENT_SECRET"):
        create_app()


def test_google_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("APP_GOOGLE_CLIENT_SECRET", "client-secret")

    app = create_app()

    assert (app.config["GOOGLE_CLIENT_ID"], app.config["GOOGLE_CLIENT_SECRET"]) == (
        "client-id",
        "client-secret",
    )


def test_google_login_when_logged_in_goes_to_the_collection(
    google_app: Flask, google_user: User, google_answers: Answer
):
    google_answers(_claims())
    client = google_app.test_client()
    client.get("/login/google/callback")

    rv = client.get("/login/google")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/"
