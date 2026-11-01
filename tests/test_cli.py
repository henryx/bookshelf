from collections.abc import Callable

import pytest
from flask import Flask

from bookshelf.db import get_db
from bookshelf.models.users import (
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
        args=["create-admin", "Admin@Example.com"], input="s3cret-pw\ns3cret-pw\n"
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
        args=["create-admin", user.email], input="pw\npw\n"
    )

    assert rv.exit_code != 0
    assert f"User {user.email} already exists" in rv.output


def test_create_admin_refused_when_admin_exists(db_app: Flask, admin: User):
    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", "second@example.com"], input="pw\npw\n"
    )

    assert rv.exit_code != 0
    assert "An admin already exists" in rv.output
    assert not _exists(db_app, "second@example.com")


def test_create_admin_refused_when_only_locked_admin_exists(
    db_app: Flask, admin: User, lock_user: Callable[[User], None]
):
    lock_user(admin)

    rv = db_app.test_cli_runner().invoke(
        args=["create-admin", "second@example.com"], input="pw\npw\n"
    )

    assert rv.exit_code != 0
    assert "An admin already exists" in rv.output


ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin-pw"


@pytest.fixture
def admin(db_app: Flask) -> User:
    with db_app.app_context():
        return create_user(get_db(), ADMIN_EMAIL, ADMIN_PASSWORD, role=Role.ADMIN)


def _reset(
    db_app: Flask, email: str, admin_password: str, new_password: str = "new-pw"
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
        assert authenticate(conn, user.email, "new-pw") is not None
        assert authenticate(conn, user.email, password) is None


def test_reset_user_password_prompts_for_admin_email(
    db_app: Flask, admin: User, user: User
):
    rv = db_app.test_cli_runner().invoke(
        args=["reset-user-password", user.email],
        input=f"{ADMIN_EMAIL}\n{ADMIN_PASSWORD}\nnew-pw\nnew-pw\n",
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
        input=f"{password}\nnew-pw\nnew-pw\n",
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
        other = create_user(get_db(), "other@example.com", "pw")

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
        all(column in line for column in ("Email", "Role", "Status")) for line in lines
    )
    admin_line = next(line for line in lines if ADMIN_EMAIL in line)
    user_line = next(line for line in lines if user.email in line)
    assert "ADMIN" in admin_line
    assert "active" in admin_line
    assert "REGULAR" in user_line
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
        other = create_user(get_db(), "other@example.com", "pw")

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


def _create(db_app: Flask, email: str, admin_password: str, password: str = "user-pw"):
    return db_app.test_cli_runner().invoke(
        args=["create-user", email, "--admin-email", ADMIN_EMAIL],
        input=f"{admin_password}\n{password}\n{password}\n",
    )


def test_create_user(db_app: Flask, admin: User):
    rv = _create(db_app, "New.Reader@Example.com", ADMIN_PASSWORD)

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        conn = get_db()
        created = authenticate(conn, "new.reader@example.com", "user-pw")
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
        input=f"{password}\npw\npw\n",
    )

    assert rv.exit_code != 0
    assert "Invalid admin credentials" in rv.output
    assert not _exists(db_app, "new@example.com")
