from flask import Flask

from bookshelf.db import get_db
from bookshelf.models.users import Role, User, authenticate, find_by_email


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


def test_reset_admin_password(db_app: Flask):
    runner = db_app.test_cli_runner()
    runner.invoke(args=["create-admin", "admin@example.com"], input="old-pw\nold-pw\n")

    rv = runner.invoke(
        args=["reset-admin-password", "Admin@Example.com"], input="new-pw\nnew-pw\n"
    )

    assert rv.exit_code == 0, rv.output
    with db_app.app_context():
        conn = get_db()
        assert authenticate(conn, "admin@example.com", "new-pw") is not None
        assert authenticate(conn, "admin@example.com", "old-pw") is None


def test_reset_admin_password_rejects_unknown_email(db_app: Flask):
    rv = db_app.test_cli_runner().invoke(
        args=["reset-admin-password", "nobody@example.com"], input="pw\npw\n"
    )

    assert rv.exit_code != 0
    assert "not found" in rv.output


def test_reset_admin_password_rejects_regular_user(db_app: Flask, user: User):
    rv = db_app.test_cli_runner().invoke(
        args=["reset-admin-password", user.email], input="pw\npw\n"
    )

    assert rv.exit_code != 0
    assert "not an admin" in rv.output
