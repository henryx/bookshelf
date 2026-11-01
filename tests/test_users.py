from collections.abc import Callable

import pytest
from firebird.driver import DatabaseError
from flask import Flask

from bookshelf.db import get_db
from bookshelf.models.users import (
    Role,
    User,
    admin_exists,
    authenticate,
    create_user,
    delete_user,
    find_by_email,
    list_users,
    set_locked,
    set_password,
)


def test_password_is_stored_as_argon2id_hash(user: User, password: str):
    assert user.password_hash.startswith("$argon2id$")
    assert password not in user.password_hash


def test_same_password_gets_different_salts(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        other = create_user(get_db(), "other@example.com", password)

    assert other.password_hash != user.password_hash


def test_email_is_normalised(user: User):
    assert user.email == "reader@example.com"


def test_authenticate_with_correct_password(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        found = authenticate(get_db(), " READER@example.com ", password)

    assert found is not None
    assert found.id == user.id


def test_authenticate_rejects_wrong_password(db_app: Flask, user: User):
    with db_app.app_context():
        assert authenticate(get_db(), user.email, "wrong") is None


def test_authenticate_rejects_unknown_email(db_app: Flask, password: str):
    with db_app.app_context():
        assert authenticate(get_db(), "nobody@example.com", password) is None


def test_authenticate_rejects_locked_user(
    db_app: Flask, user: User, password: str, lock_user: Callable[[User], None]
):
    lock_user(user)

    with db_app.app_context():
        assert authenticate(get_db(), user.email, password) is None


def test_new_user_is_regular_by_default(user: User):
    assert user.role is Role.REGULAR
    assert not user.is_admin


def test_create_admin_user(db_app: Flask, password: str):
    with db_app.app_context():
        admin = create_user(get_db(), "boss@example.com", password, role=Role.ADMIN)

    assert admin.role is Role.ADMIN
    assert admin.is_admin


def test_database_rejects_unknown_role(db_app: Flask, user: User):
    with db_app.app_context():
        conn = get_db()
        with (
            pytest.raises(DatabaseError, match="CHK_USERS_ROLE"),
            conn.cursor() as cur,
        ):
            cur.execute("UPDATE USERS SET ROLE = 'SUPERUSER' WHERE ID = ?", (user.id,))
        conn.rollback()


def test_set_password_replaces_hash(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        conn = get_db()
        set_password(conn, user, "brand new password")

        assert authenticate(conn, user.email, "brand new password") is not None
        assert authenticate(conn, user.email, password) is None


def test_set_locked_toggles_lock(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        conn = get_db()

        set_locked(conn, user, locked=True)
        assert authenticate(conn, user.email, password) is None

        set_locked(conn, user, locked=False)
        assert authenticate(conn, user.email, password) is not None


def test_list_users_sorted_by_email(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        conn = get_db()
        create_user(conn, "aaa@example.com", password)

        emails = [u.email for u in list_users(conn)]

    assert emails == ["aaa@example.com", user.email]


def test_delete_user_removes_row(db_app: Flask, user: User):
    with db_app.app_context():
        conn = get_db()
        delete_user(conn, user)

        assert find_by_email(conn, user.email) is None


def test_admin_exists(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        conn = get_db()
        assert not admin_exists(conn)

        create_user(conn, "boss@example.com", password, role=Role.ADMIN)
        assert admin_exists(conn)
