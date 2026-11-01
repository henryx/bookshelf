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


def test_create_admin_rejects_existing_email(db_app: Flask):
    runner = db_app.test_cli_runner()
    args = ["create-admin", "admin@example.com"]
    runner.invoke(args=args, input="pw\npw\n")

    rv = runner.invoke(args=args, input="pw\npw\n")

    assert rv.exit_code != 0
    assert "already exists" in rv.output


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
