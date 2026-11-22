from collections.abc import Callable

import pytest
from flask import Flask

from bookshelf.db import get_db
from bookshelf.models.users import (
    AuthMethod,
    LastAdminError,
    Role,
    User,
    authenticate,
    create_user,
    find_by_email,
)


def test_init_db_is_idempotent(db_app: Flask):
    runner = db_app.test_cli_runner()

    assert runner.invoke(args=["init-db"]).exit_code == 0
    assert runner.invoke(args=["init-db"]).exit_code == 0


def test_create_admin(db_app: Flask):
    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", "Admin@Example.com"],
        input=f"{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        admin = find_by_email(get_db(), "admin@example.com")
    assert admin is not None
    assert admin.role is Role.ADMIN


def test_create_admin_does_not_accept_password_option(db_app: Flask):
    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", "admin@example.com", "--password", "pw"]
    )

    assert rv.exit_code != 0
    assert "No such option" in rv.output
    with db_app.app_context():
        assert find_by_email(get_db(), "admin@example.com") is None


def test_create_admin_rejects_existing_email(db_app: Flask, user: User):
    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", user.email], input=f"{NEW_PASSWORD}\n{NEW_PASSWORD}\n"
    )

    assert rv.exit_code != 0
    assert f"User {user.email} already exists" in rv.output


def test_create_admin_refused_when_admin_exists(db_app: Flask, admin: User):
    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", "second@example.com"],
        input=f"{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code != 0
    assert "An admin already exists" in rv.output
    assert not _exists(db_app, "second@example.com")


def test_create_admin_refused_when_only_locked_admin_exists(
    db_app: Flask, admin: User, lock_user: Callable[[User], None]
):
    lock_user(admin)

    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", "second@example.com"],
        input=f"{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code != 0
    assert "An admin already exists" in rv.output


ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin-password"
NEW_PASSWORD = "new-password-1"


@pytest.fixture
def admin(db_app: Flask) -> User:
    with db_app.app_context():
        return create_user(get_db(), ADMIN_EMAIL, ADMIN_PASSWORD, role=Role.ADMIN)


def _reset(
    db_app: Flask, email: str, admin_password: str, new_password: str = NEW_PASSWORD
):
    return db_app.test_cli_runner().invoke(
        args=["reset-user-password", email, "--admin-email", ADMIN_EMAIL],
        input=f"{admin_password}\n{new_password}\n{new_password}\n",
    )


def test_reset_user_password(db_app: Flask, admin: User, user: User, password: str):
    rv = _reset(db_app, user.email.upper(), ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        conn = get_db()
        assert authenticate(conn, user.email, NEW_PASSWORD) is not None
        assert authenticate(conn, user.email, password) is None


def test_reset_user_password_prompts_for_admin_email(
    db_app: Flask, admin: User, user: User
):
    rv = db_app.test_cli_runner().invoke(
        args=["reset-user-password", user.email],
        input=f"{ADMIN_EMAIL}\n{ADMIN_PASSWORD}\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output


def test_reset_user_password_rejects_wrong_admin_password(
    db_app: Flask, admin: User, user: User, password: str
):
    rv = _reset(db_app, user.email, "wrong")

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    with db_app.app_context():
        assert authenticate(get_db(), user.email, password) is not None


def test_reset_user_password_rejects_non_admin(
    db_app: Flask, user: User, password: str
):
    rv = db_app.test_cli_runner().invoke(
        args=["reset-user-password", user.email, "--admin-email", user.email],
        input=f"{password}\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output


def test_reset_user_password_rejects_locked_admin(
    db_app: Flask,
    admin: User,
    user: User,
    lock_user: Callable[[User], None],
):
    lock_user(admin)

    rv = _reset(db_app, user.email, ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output


def test_reset_user_password_rejects_unknown_email(db_app: Flask, admin: User):
    rv = _reset(db_app, "nobody@example.com", ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "not found" in rv.output


def _run_as_admin(db_app: Flask, command: str, email: str, admin_password: str):
    return db_app.test_cli_runner().invoke(
        args=[command, email, "--admin-email", ADMIN_EMAIL],
        input=f"{admin_password}\n",
    )


def test_lock_user(db_app: Flask, admin: User, user: User, password: str):
    rv = _run_as_admin(db_app, "lock-user", user.email, ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        conn = get_db()
        locked = find_by_email(conn, user.email)
        assert locked is not None
        assert locked.is_locked
        assert authenticate(conn, user.email, password) is None


def test_unlock_user(
    db_app: Flask,
    admin: User,
    user: User,
    password: str,
    lock_user: Callable[[User], None],
):
    lock_user(user)

    rv = _run_as_admin(db_app, "unlock-user", user.email, ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        assert authenticate(get_db(), user.email, password) is not None


@pytest.mark.parametrize("command", ["lock-user", "unlock-user"])
def test_lock_commands_reject_wrong_admin_password(
    db_app: Flask, admin: User, user: User, command: str
):
    rv = _run_as_admin(db_app, command, user.email, "wrong")

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output


@pytest.mark.parametrize("command", ["lock-user", "unlock-user"])
def test_lock_commands_reject_non_admin(
    db_app: Flask, user: User, password: str, command: str
):
    with db_app.app_context():
        other = create_user(get_db(), "other@example.com", NEW_PASSWORD)

    rv = db_app.test_cli_runner().invoke(
        args=[command, other.email, "--admin-email", user.email],
        input=f"{password}\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output


@pytest.mark.parametrize("command", ["lock-user", "unlock-user"])
def test_lock_commands_reject_unknown_email(db_app: Flask, admin: User, command: str):
    rv = _run_as_admin(db_app, command, "nobody@example.com", ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "not found" in rv.output


def test_admin_cannot_lock_themselves(db_app: Flask, admin: User):
    rv = _run_as_admin(db_app, "lock-user", ADMIN_EMAIL, ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "cannot lock your own account" in rv.output
    with db_app.app_context():
        assert authenticate(get_db(), ADMIN_EMAIL, ADMIN_PASSWORD) is not None


def test_list_users(
    db_app: Flask, admin: User, user: User, lock_user: Callable[[User], None]
):
    lock_user(user)

    rv = db_app.test_cli_runner().invoke(
        args=["list-users", "--admin-email", ADMIN_EMAIL],
        input=f"{ADMIN_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output
    lines = rv.output.splitlines()
    assert any(
        all(column in line for column in ("Email", "Role", "Login", "Status"))
        for line in lines
    )
    admin_line = next(line for line in lines if ADMIN_EMAIL in line)
    user_line = next(line for line in lines if user.email in line)
    assert "ADMIN" in admin_line
    assert "active" in admin_line
    assert "REGULAR" in user_line
    assert "local" in user_line
    assert "locked" in user_line
    assert lines.index(admin_line) < lines.index(user_line)


def test_list_users_requires_admin(db_app: Flask, user: User, password: str):
    rv = db_app.test_cli_runner().invoke(
        args=["list-users", "--admin-email", user.email],
        input=f"{password}\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert "REGULAR" not in rv.output


def _delete(db_app: Flask, email: str, admin_password: str, confirm: str = "y"):
    return db_app.test_cli_runner().invoke(
        args=["delete-user", email, "--admin-email", ADMIN_EMAIL],
        input=f"{admin_password}\n{confirm}\n",
    )


def _exists(db_app: Flask, email: str) -> bool:
    with db_app.app_context():
        return find_by_email(get_db(), email) is not None


def test_delete_user(db_app: Flask, admin: User, user: User):
    rv = _delete(db_app, user.email, ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    assert not _exists(db_app, user.email)


def test_delete_user_can_be_cancelled(db_app: Flask, admin: User, user: User):
    rv = _delete(db_app, user.email, ADMIN_PASSWORD, confirm="n")

    assert rv.exit_code != 0
    assert _exists(db_app, user.email)


def test_delete_user_yes_skips_confirmation(db_app: Flask, admin: User, user: User):
    rv = db_app.test_cli_runner().invoke(
        args=["delete-user", user.email, "--admin-email", ADMIN_EMAIL, "--yes"],
        input=f"{ADMIN_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output
    assert not _exists(db_app, user.email)


def test_delete_user_rejects_wrong_admin_password(
    db_app: Flask, admin: User, user: User
):
    rv = _delete(db_app, user.email, "wrong")

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert _exists(db_app, user.email)


def test_delete_user_rejects_non_admin(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        other = create_user(get_db(), "other@example.com", NEW_PASSWORD)

    rv = db_app.test_cli_runner().invoke(
        args=["delete-user", other.email, "--admin-email", user.email],
        input=f"{password}\ny\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert _exists(db_app, other.email)


def test_delete_user_rejects_unknown_email(db_app: Flask, admin: User):
    rv = _delete(db_app, "nobody@example.com", ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "not found" in rv.output


def test_admin_cannot_delete_themselves(db_app: Flask, admin: User):
    rv = _delete(db_app, ADMIN_EMAIL, ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "cannot delete your own account" in rv.output
    assert _exists(db_app, ADMIN_EMAIL)


def _create(
    db_app: Flask, email: str, admin_password: str, password: str = "user-password"
):
    return db_app.test_cli_runner().invoke(
        args=["create-user", email, "--admin-email", ADMIN_EMAIL],
        input=f"{admin_password}\n{password}\n{password}\n",
    )


def test_create_user(db_app: Flask, admin: User):
    rv = _create(db_app, "New.Reader@Example.com", ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        conn = get_db()
        created = authenticate(conn, "new.reader@example.com", "user-password")
    assert created is not None
    assert created.role is Role.REGULAR


def test_create_user_rejects_existing_email(db_app: Flask, admin: User, user: User):
    rv = _create(db_app, user.email.upper(), ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "already exists" in rv.output


def test_create_user_rejects_wrong_admin_password(db_app: Flask, admin: User):
    rv = _create(db_app, "new@example.com", "wrong")

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert not _exists(db_app, "new@example.com")


def test_create_user_rejects_non_admin(db_app: Flask, user: User, password: str):
    rv = db_app.test_cli_runner().invoke(
        args=["create-user", "new@example.com", "--admin-email", user.email],
        input=f"{password}\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert not _exists(db_app, "new@example.com")


def test_promote_user(db_app: Flask, admin: User, user: User, password: str):
    rv = _run_as_admin(db_app, "promote-user", user.email, ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        promoted = find_by_email(get_db(), user.email)
    assert promoted is not None
    assert promoted.role is Role.ADMIN

    # the promoted user can now authorise admin commands
    rv = db_app.test_cli_runner().invoke(
        args=["list-users", "--admin-email", user.email], input=f"{password}\n"
    )
    assert rv.exit_code == 0, rv.output


def test_promote_user_already_admin(db_app: Flask, admin: User):
    rv = _run_as_admin(db_app, "promote-user", ADMIN_EMAIL, ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    assert "already an admin" in rv.output


def test_promote_user_rejects_wrong_admin_password(
    db_app: Flask, admin: User, user: User
):
    rv = _run_as_admin(db_app, "promote-user", user.email, "wrong")

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    with db_app.app_context():
        unchanged = find_by_email(get_db(), user.email)
    assert unchanged is not None
    assert unchanged.role is Role.REGULAR


def test_promote_user_rejects_non_admin(db_app: Flask, user: User, password: str):
    rv = db_app.test_cli_runner().invoke(
        args=["promote-user", user.email, "--admin-email", user.email],
        input=f"{password}\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output


def test_promote_user_rejects_unknown_email(db_app: Flask, admin: User):
    rv = _run_as_admin(db_app, "promote-user", "nobody@example.com", ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "not found" in rv.output


def _role(db_app: Flask, email: str) -> Role:
    with db_app.app_context():
        found = find_by_email(get_db(), email)
    assert found is not None
    return found.role


def test_demote_user(db_app: Flask, admin: User, password: str):
    with db_app.app_context():
        other = create_user(
            get_db(), "other.admin@example.com", password, role=Role.ADMIN
        )

    rv = _run_as_admin(db_app, "demote-user", other.email, ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    assert _role(db_app, other.email) is Role.REGULAR


def test_demote_user_already_regular(db_app: Flask, admin: User, user: User):
    rv = _run_as_admin(db_app, "demote-user", user.email, ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    assert "not an admin" in rv.output


def test_admin_cannot_demote_themselves(db_app: Flask, admin: User):
    rv = _run_as_admin(db_app, "demote-user", ADMIN_EMAIL, ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "cannot demote your own account" in rv.output
    assert _role(db_app, ADMIN_EMAIL) is Role.ADMIN


def test_demote_user_rejects_wrong_admin_password(
    db_app: Flask, admin: User, password: str
):
    with db_app.app_context():
        other = create_user(
            get_db(), "other.admin@example.com", password, role=Role.ADMIN
        )

    rv = _run_as_admin(db_app, "demote-user", other.email, "wrong")

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert _role(db_app, other.email) is Role.ADMIN


def test_demote_user_rejects_non_admin(
    db_app: Flask, admin: User, user: User, password: str
):
    rv = db_app.test_cli_runner().invoke(
        args=["demote-user", ADMIN_EMAIL, "--admin-email", user.email],
        input=f"{password}\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert _role(db_app, ADMIN_EMAIL) is Role.ADMIN


def test_demote_user_rejects_unknown_email(db_app: Flask, admin: User):
    rv = _run_as_admin(db_app, "demote-user", "nobody@example.com", ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "not found" in rv.output


def _skip_existence_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a user created by someone else after the CLI checked."""
    monkeypatch.setattr("bookshelf.cli.find_by_email", lambda conn, email: None)


def test_create_user_reports_user_created_concurrently(
    db_app: Flask, admin: User, user: User, monkeypatch: pytest.MonkeyPatch
):
    _skip_existence_check(monkeypatch)

    rv = _create(db_app, user.email, ADMIN_PASSWORD)

    assert rv.exit_code == 1
    assert f"User {user.email} already exists" in rv.output


def test_create_admin_reports_user_created_concurrently(
    db_app: Flask, user: User, monkeypatch: pytest.MonkeyPatch
):
    _skip_existence_check(monkeypatch)

    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", user.email], input=f"{NEW_PASSWORD}\n{NEW_PASSWORD}\n"
    )

    assert rv.exit_code == 1
    assert f"User {user.email} already exists" in rv.output


@pytest.mark.parametrize("email", ["not-an-email", "admin@bookshelf.local", "x@nas"])
def test_create_admin_rejects_invalid_email(db_app: Flask, email: str):
    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", email], input=f"{NEW_PASSWORD}\n{NEW_PASSWORD}\n"
    )

    assert rv.exit_code == 2
    assert "Invalid value for 'EMAIL'" in rv.output
    assert not _exists(db_app, email)


def test_create_user_rejects_invalid_email(db_app: Flask, admin: User):
    rv = _create(db_app, "reader@bookshelf.local", ADMIN_PASSWORD)

    assert rv.exit_code == 2
    assert "Invalid value for 'EMAIL'" in rv.output
    assert not _exists(db_app, "reader@bookshelf.local")


def _add_failure(app: Flask, email: str, minutes_ago: int) -> None:
    with app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO LOGIN_FAILURES (EMAIL, IP_ADDRESS, ATTEMPTED_AT) "
                "VALUES (?, ?, DATEADD(? MINUTE TO CURRENT_TIMESTAMP))",
                (email, "192.0.2.1", -minutes_ago),
            )
        conn.commit()


def _failure_emails(app: Flask) -> list[str]:
    with app.app_context(), get_db().cursor() as cur:
        cur.execute("SELECT EMAIL FROM LOGIN_FAILURES ORDER BY EMAIL")
        return [email for (email,) in cur.fetchall()]


def test_prune_login_failures_removes_only_expired(db_app: Flask):
    _add_failure(db_app, "old1@example.com", minutes_ago=16)
    _add_failure(db_app, "old2@example.com", minutes_ago=60)
    _add_failure(db_app, "recent@example.com", minutes_ago=14)

    rv = db_app.test_cli_runner().invoke(args=["prune-login-failures"])

    assert rv.exit_code == 0, rv.output
    assert "Removed 2 expired login failures." in rv.output
    assert _failure_emails(db_app) == ["recent@example.com"]


def test_prune_login_failures_uses_configured_window(db_app: Flask):
    db_app.config["LOGIN_BLOCK_MINUTES"] = 60
    _add_failure(db_app, "old@example.com", minutes_ago=61)
    _add_failure(db_app, "recent@example.com", minutes_ago=30)

    rv = db_app.test_cli_runner().invoke(args=["prune-login-failures"])

    assert rv.exit_code == 0, rv.output
    assert _failure_emails(db_app) == ["recent@example.com"]


SHORT = "too-short"


def test_create_admin_reprompts_for_short_password(db_app: Flask):
    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", "admin@example.com"],
        input=f"{SHORT}\n{SHORT}\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output
    assert "at least 12 characters" in rv.output
    with db_app.app_context():
        assert authenticate(get_db(), "admin@example.com", NEW_PASSWORD) is not None


def test_create_user_refuses_short_password(db_app: Flask, admin: User):
    rv = _create(db_app, "new@example.com", ADMIN_PASSWORD, password=SHORT)

    assert rv.exit_code != 0
    assert "at least 12 characters" in rv.output
    assert not _exists(db_app, "new@example.com")


def test_reset_user_password_refuses_short_password(
    db_app: Flask, admin: User, user: User, password: str
):
    rv = _reset(db_app, user.email, ADMIN_PASSWORD, new_password=SHORT)

    assert rv.exit_code != 0
    assert "at least 12 characters" in rv.output
    with db_app.app_context():
        assert authenticate(get_db(), user.email, password) is not None


def test_new_password_uses_configured_min_length(db_app: Flask, admin: User):
    db_app.config["MIN_PASSWORD_LENGTH"] = 20

    rv = _create(db_app, "new@example.com", ADMIN_PASSWORD, password="x" * 19)

    assert rv.exit_code != 0
    assert "at least 20 characters" in rv.output
    assert not _exists(db_app, "new@example.com")


def _last_admin(_conn: object, user: User, *_args: object) -> None:
    """Stands in for a removal that would leave no admin (a concurrent one)."""
    raise LastAdminError(user.email)


@pytest.mark.parametrize(
    ("command", "target"), [("demote-user", "set_role"), ("delete-user", "delete_user")]
)
def test_removing_the_only_admin_left_is_refused(
    db_app: Flask,
    admin: User,
    password: str,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    target: str,
):
    with db_app.app_context():
        other = create_user(get_db(), "second@example.com", password, role=Role.ADMIN)
    monkeypatch.setattr(f"bookshelf.cli.{target}", _last_admin)

    args = [command, other.email, "--admin-email", ADMIN_EMAIL]
    if command == "delete-user":
        args.append("--yes")
    rv = db_app.test_cli_runner().invoke(args=args, input=f"{ADMIN_PASSWORD}\n")

    assert rv.exit_code != 0
    assert "only admin left" in rv.output


# Google authentication


def _google_user(db_app: Flask, email: str = "gina@example.com") -> User:
    with db_app.app_context():
        return create_user(get_db(), email, None, auth_method=AuthMethod.GOOGLE)


def _find(db_app: Flask, email: str) -> User:
    with db_app.app_context():
        user = find_by_email(get_db(), email)
    assert user is not None
    return user


def test_create_user_with_google_asks_no_password(db_app: Flask, admin: User):
    rv = db_app.test_cli_runner().invoke(
        args=["create-user", "Gina@Example.com", "--auth", "google"],
        input=f"{ADMIN_EMAIL}\n{ADMIN_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output
    assert "Password" not in rv.output.replace("Admin password", "")
    assert "logs in with Google" in rv.output
    created = _find(db_app, "gina@example.com")
    assert (created.auth_method, created.password_hash) == (AuthMethod.GOOGLE, None)


def test_create_user_is_local_by_default(db_app: Flask, admin: User):
    _create(db_app, "new@example.com", ADMIN_PASSWORD)

    assert _find(db_app, "new@example.com").auth_method is AuthMethod.LOCAL


def test_create_user_rejects_an_unknown_auth_method(db_app: Flask, admin: User):
    rv = db_app.test_cli_runner().invoke(
        args=["create-user", "new@example.com", "--auth", "github"]
    )

    assert rv.exit_code != 0
    assert "Invalid value for '--auth'" in rv.output


def test_set_auth_method_to_google(db_app: Flask, admin: User, user: User):
    rv = db_app.test_cli_runner().invoke(
        args=["set-auth-method", user.email, "google", "--admin-email", ADMIN_EMAIL],
        input=f"{ADMIN_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output
    assert f"{user.email} now logs in with Google" in rv.output
    assert _find(db_app, user.email).auth_method is AuthMethod.GOOGLE


def test_set_auth_method_to_local_asks_for_a_password(db_app: Flask, admin: User):
    google = _google_user(db_app)

    rv = db_app.test_cli_runner().invoke(
        args=["set-auth-method", google.email, "local", "--admin-email", ADMIN_EMAIL],
        input=f"{ADMIN_PASSWORD}\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n",
    )

    assert rv.exit_code == 0, rv.output
    assert f"{google.email} now logs in with a password" in rv.output
    with db_app.app_context():
        assert authenticate(get_db(), google.email, NEW_PASSWORD) is not None


def test_set_auth_method_requires_admin(db_app: Flask, user: User, password: str):
    rv = db_app.test_cli_runner().invoke(
        args=["set-auth-method", user.email, "google", "--admin-email", user.email],
        input=f"{password}\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert _find(db_app, user.email).auth_method is AuthMethod.LOCAL


def test_reset_password_of_a_google_user_is_refused(db_app: Flask, admin: User):
    google = _google_user(db_app)

    rv = _reset(db_app, google.email, ADMIN_PASSWORD)

    assert rv.exit_code != 0
    assert "logs in with Google" in rv.output
    assert "set-auth-method" in rv.output


def test_list_users_shows_google_users(db_app: Flask, admin: User):
    _google_user(db_app)

    rv = db_app.test_cli_runner().invoke(
        args=["list-users", "--admin-email", ADMIN_EMAIL], input=f"{ADMIN_PASSWORD}\n"
    )

    line = next(line for line in rv.output.splitlines() if "gina@example.com" in line)
    assert "google" in line
