from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from flask import Flask
from flask.testing import FlaskClient

from bookshelf import create_app
from bookshelf.db import get_db
from bookshelf.models.users import (
    Role,
    User,
    clear_login_failures,
    delete_user,
    set_password,
    set_role,
)

HTMX = {"HX-Request": "true"}


def _login(client: FlaskClient, email: str, password: str, **kwargs):
    return client.post("/login", data={"email": email, "password": password}, **kwargs)


def test_main_page_requires_login(db_client: FlaskClient):
    rv = db_client.get("/")

    assert rv.status_code == 302
    assert rv.headers["Location"].startswith("/login")


def test_htmx_request_without_login_redirects_via_header(db_client: FlaskClient):
    rv = db_client.get("/", headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/login"


def test_htmx_logout_with_expired_session_redirects_via_header(
    db_client: FlaskClient,
):
    rv = db_client.post("/logout", headers=HTMX)

    assert rv.status_code == 204
    assert rv.headers["HX-Redirect"] == "/login"


def test_login_page_renders_form(db_client: FlaskClient):
    rv = db_client.get("/login")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" in html
    assert 'name="email"' in html
    assert 'name="password"' in html
    assert "hx-indicator" in html


def test_login_success_sets_persistent_cookie(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/collection/"
    assert any("remember_token=" in c for c in rv.headers.getlist("Set-Cookie"))
    assert db_client.get("/").status_code == 200


def test_login_failure_shows_error(db_client: FlaskClient, user: User, password: str):
    rv = _login(db_client, user.email, "wrong")

    assert rv.status_code == 200
    assert "Invalid email or password" in rv.get_data(as_text=True)
    assert db_client.get("/").status_code == 302


def test_locked_user_cannot_login(
    db_client: FlaskClient, user: User, password: str, lock_user: Callable[[User], None]
):
    lock_user(user)

    rv = _login(db_client, user.email, password)

    assert "Invalid email or password" in rv.get_data(as_text=True)


TOO_MANY_ATTEMPTS = "Too many failed login attempts"


CLIENT_IP = "127.0.0.1"  # test client default
OTHER_IP = {"environ_base": {"REMOTE_ADDR": "198.51.100.9"}}


def _fail_logins(client: FlaskClient, email: str, times: int, **kwargs) -> None:
    for _ in range(times):
        _login(client, email, "wrong", **kwargs)


def test_login_blocked_after_too_many_failures(
    db_client: FlaskClient, user: User, password: str
):
    _fail_logins(db_client, user.email, 5)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 429
    assert TOO_MANY_ATTEMPTS in rv.get_data(as_text=True)
    assert db_client.get("/").status_code == 302


def test_login_block_applies_to_unknown_email(db_client: FlaskClient):
    _fail_logins(db_client, "nobody@example.com", 5)

    rv = _login(db_client, "nobody@example.com", "wrong")

    assert rv.status_code == 429
    assert TOO_MANY_ATTEMPTS in rv.get_data(as_text=True)


def test_login_block_is_per_email(db_client: FlaskClient, user: User, password: str):
    _fail_logins(db_client, "nobody@example.com", 5)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def test_login_block_is_per_ip(db_client: FlaskClient, user: User, password: str):
    """Failures from an attacker's IP do not lock the real user out."""
    _fail_logins(db_client, user.email, 5, **OTHER_IP)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def test_ip_blocked_after_too_many_failures_across_emails(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    db_app.config["LOGIN_MAX_IP_FAILURES"] = 3
    for n in range(3):
        _login(db_client, f"guess{n}@example.com", "wrong")

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 429
    assert TOO_MANY_ATTEMPTS in rv.get_data(as_text=True)


def test_ip_block_does_not_affect_other_ips(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    db_app.config["LOGIN_MAX_IP_FAILURES"] = 3
    for n in range(3):
        _login(db_client, f"guess{n}@example.com", "wrong", **OTHER_IP)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def _add_old_failures(app: Flask, email: str, times: int, minutes_ago: int) -> None:
    with app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            for _ in range(times):
                cur.execute(
                    "INSERT INTO LOGIN_FAILURES (ID, EMAIL, IP_ADDRESS, ATTEMPTED_AT) "
                    "VALUES ((SELECT ID FROM USERS WHERE EMAIL = ?), ?, ?, "
                    "DATEADD(? MINUTE TO CURRENT_TIMESTAMP))",
                    (email, email, CLIENT_IP, -minutes_ago),
                )
        conn.commit()


def test_login_failures_expire(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    _add_old_failures(db_app, user.email, 5, minutes_ago=16)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def test_login_block_uses_configured_max_failures(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    db_app.config["LOGIN_MAX_FAILURES"] = 2
    _fail_logins(db_client, user.email, 2)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 429


def test_login_block_uses_configured_minutes(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    db_app.config["LOGIN_BLOCK_MINUTES"] = 10
    _add_old_failures(db_app, user.email, 5, minutes_ago=11)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def test_successful_login_clears_failures(
    db_client: FlaskClient, user: User, password: str
):
    _fail_logins(db_client, user.email, 4)
    _login(db_client, user.email, password)
    db_client.post("/logout")
    _fail_logins(db_client, user.email, 1)

    rv = _login(db_client, user.email, password)

    assert rv.status_code == 302


def test_htmx_login_success_redirects_via_header(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, user.email, password, headers=HTMX)

    assert rv.headers["HX-Redirect"] == "/collection/"


def test_htmx_login_failure_returns_partial(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, user.email, "wrong", headers=HTMX)
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "<html" not in html
    assert "Invalid email or password" in html


def test_logout_returns_to_login(db_client: FlaskClient, user: User, password: str):
    _login(db_client, user.email, password)

    rv = db_client.post("/logout")

    assert rv.status_code == 302
    assert rv.headers["Location"] == "/login"
    assert db_client.get("/").status_code == 302


def test_locking_user_ends_existing_session(
    db_client: FlaskClient,
    user: User,
    password: str,
    lock_user: Callable[[User], None],
):
    _login(db_client, user.email, password)
    lock_user(user)

    assert db_client.get("/").status_code == 302


def test_password_reset_ends_existing_session(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    _login(db_client, user.email, password)
    with db_app.app_context():
        set_password(get_db(), user, "a brand new password")

    assert db_client.get("/").status_code == 302


def test_role_change_keeps_existing_session(
    db_client: FlaskClient, db_app: Flask, user: User, password: str
):
    _login(db_client, user.email, password)
    with db_app.app_context():
        set_role(get_db(), user, Role.ADMIN)

    assert db_client.get("/").status_code == 200


def test_login_requires_csrf_token(user: User, password: str):
    client = create_app({"TESTING": True}).test_client()

    rv = _login(client, user.email, password)

    assert rv.status_code == 400


def test_failed_login_logs_email_and_ip(db_client: FlaskClient, user: User, caplog):
    _login(db_client, user.email, "wrong")

    assert f"Failed login for {user.email} from 127.0.0.1" in caplog.messages


def test_blocked_login_logs_email_and_ip(db_client: FlaskClient, user: User, caplog):
    _fail_logins(db_client, user.email, 5)
    caplog.clear()

    _login(db_client, user.email, "wrong")

    assert any(
        m.startswith(f"Blocked login for {user.email} from 127.0.0.1")
        for m in caplog.messages
    )


def test_login_rejects_invalid_email_without_counting_it(
    db_client: FlaskClient, caplog
):
    rv = _login(db_client, "correct horse battery staple", "wrong")

    assert rv.status_code == 200
    assert "Invalid email address" in rv.get_data(as_text=True)
    assert not any("Failed login" in m for m in caplog.messages)


def test_login_ignores_spaces_around_email(
    db_client: FlaskClient, user: User, password: str
):
    rv = _login(db_client, f"  {user.email}  ", password)

    assert rv.status_code == 302


def test_concurrent_failed_logins_cannot_exceed_the_limit(db_app: Flask, user: User):
    """
    Attempts sent at once must not all pass the check before any is counted,
    and refused ones, removing rows that may look identical, must not fail.
    """
    attempts, limit = 10, 5
    start = Barrier(attempts)

    def attempt(_: int) -> int:
        client = db_app.test_client()
        start.wait()
        return _login(client, user.email, "wrong").status_code

    # Conflicts are a race, so give them several chances to happen.
    for _ in range(5):
        with db_app.app_context():
            clear_login_failures(get_db(), user.email, CLIENT_IP)
        with ThreadPoolExecutor(max_workers=attempts) as pool:
            statuses = list(pool.map(attempt, range(attempts)))

        assert set(statuses) <= {200, 429}, statuses
        checked = [s for s in statuses if s == 200]
        assert len(checked) <= limit, statuses
        with db_app.app_context():
            assert _failures(user.email) == len(checked)


def test_concurrent_logins_with_expired_failures_succeed(db_app: Flask):
    """Pruning expired failures must not make concurrent attempts conflict."""
    emails = [f"reader{n}@example.com" for n in range(8)]
    # All attempts share one IP; keep its limit out of the way.
    db_app.config["LOGIN_MAX_IP_FAILURES"] = 1000
    start = Barrier(len(emails))

    def attempt(email: str) -> int:
        client = db_app.test_client()
        start.wait()
        return _login(client, email, "wrong").status_code

    # The conflict is a race, so give it several chances to happen.
    for _ in range(5):
        for email in emails:
            _add_old_failures(db_app, email, 3, minutes_ago=30)
        with ThreadPoolExecutor(max_workers=len(emails)) as pool:
            statuses = list(pool.map(attempt, emails))

        assert statuses == [200] * len(emails)


def test_login_prunes_only_own_expired_failures(db_client: FlaskClient, user: User):
    _add_old_failures(db_client.application, user.email, 3, minutes_ago=30)
    _add_old_failures(db_client.application, "other@example.com", 3, minutes_ago=30)

    _login(db_client, user.email, "wrong")

    with db_client.application.app_context():
        assert _failures(user.email) == 1
        assert _failures("other@example.com") == 3


def test_blocked_attempt_is_not_counted(db_client: FlaskClient, user: User):
    _fail_logins(db_client, user.email, 5)

    _login(db_client, user.email, "wrong")
    _login(db_client, user.email, "wrong")

    with db_client.application.app_context():
        assert _failures(user.email) == 5


def _failures(email: str) -> int:
    with get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM LOGIN_FAILURES WHERE EMAIL = ?", (email,))
        (count,) = cur.fetchone()
    return count


def _failure_ids(email: str) -> list[bytes | None]:
    with get_db().cursor() as cur:
        cur.execute("SELECT ID FROM LOGIN_FAILURES WHERE EMAIL = ?", (email,))
        return [user_id for (user_id,) in cur.fetchall()]


def test_failure_references_user_of_known_email(db_client: FlaskClient, user: User):
    _login(db_client, user.email.upper(), "wrong")

    with db_client.application.app_context():
        assert _failure_ids(user.email) == [user.id.bytes]


def test_failure_of_unknown_email_has_no_user(db_client: FlaskClient):
    _login(db_client, "nobody@example.com", "wrong")

    with db_client.application.app_context():
        assert _failure_ids("nobody@example.com") == [None]


def test_deleting_user_deletes_their_failures(db_client: FlaskClient, user: User):
    _fail_logins(db_client, user.email, 2)

    with db_client.application.app_context():
        delete_user(get_db(), user)
        assert _failures(user.email) == 0
