import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import pytest
from firebird.driver import Connection, DatabaseError
from flask import Flask

from bookshelf.db import get_db
from bookshelf.models.users import (
    MIN_PASSWORD_LENGTH,
    AuthMethod,
    LastAdminError,
    NoPasswordError,
    PasswordTooShortError,
    Role,
    User,
    UserExistsError,
    admin_exists,
    authenticate,
    create_user,
    delete_user,
    find_by_email,
    find_by_id,
    find_by_session_id,
    google_login,
    list_users,
    login_limits,
    set_auth_method,
    set_locked,
    set_password,
    set_role,
    start_login_attempt,
    update_account,
    update_user,
)


def test_password_is_stored_as_argon2id_hash(user: User, password: str):
    assert user.password_hash is not None
    assert user.password_hash.startswith("$argon2id$")
    assert password not in user.password_hash


def test_same_password_gets_different_salts(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        other = create_user(get_db(), "other@example.com", password)

    assert other.password_hash != user.password_hash


def test_user_id_is_uuid4(user: User):
    assert isinstance(user.id, UUID)
    assert user.id.version == 4


def test_email_is_normalised(user: User):
    assert user.email == "reader@example.com"


# Name and surname


def test_new_user_has_no_name_by_default(user: User):
    assert user.name is None
    assert user.surname is None


def test_create_user_with_name_and_surname(db_app: Flask, password: str):
    with db_app.app_context():
        conn = get_db()
        create_user(conn, "ada@example.com", password, name="Ada", surname="Lovelace")
        found = find_by_email(conn, "ada@example.com")

    assert found is not None
    assert (found.name, found.surname) == ("Ada", "Lovelace")


def _named(name: str | None, surname: str | None, email: str = "reader@example.com"):
    return User(
        id=UUID(int=1),
        email=email,
        password_hash="x",
        role=Role.REGULAR,
        is_locked=False,
        name=name,
        surname=surname,
    )


@pytest.mark.parametrize(
    ("name", "surname", "full_name", "initials"),
    [
        ("Ada", "Lovelace", "Ada Lovelace", "AL"),
        (None, "Homer", "Homer", "H"),
        ("élodie", None, "élodie", "É"),
        (None, None, None, "R"),
    ],
)
def test_full_name_and_initials(name, surname, full_name, initials):
    user = _named(name, surname)

    assert user.full_name == full_name
    assert user.initials == initials


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("NAME", "", "CHK_USERS_NAME"),
        ("NAME", " Ada", "CHK_USERS_NAME"),
        ("SURNAME", "", "CHK_USERS_SURNAME"),
        ("SURNAME", "Lovelace ", "CHK_USERS_SURNAME"),
    ],
)
def test_database_rejects_blank_or_untrimmed_names(
    db_app: Flask, user: User, column: str, value: str, constraint: str
):
    # One literal query per column: identifiers are never interpolated.
    queries = {
        "NAME": "UPDATE USERS SET NAME = ? WHERE ID = ?",
        "SURNAME": "UPDATE USERS SET SURNAME = ? WHERE ID = ?",
    }
    with db_app.app_context():
        conn = get_db()
        with pytest.raises(DatabaseError, match=constraint), conn.cursor() as cur:
            cur.execute(queries[column], (value, user.id.bytes))
        conn.rollback()


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


@pytest.mark.parametrize(
    "email", ["Reader@example.com", " reader@example.com", "reader@example.com "]
)
def test_database_rejects_email_not_lowercase_and_trimmed(
    db_app: Flask, user: User, email: str
):
    with db_app.app_context():
        conn = get_db()
        with (
            pytest.raises(DatabaseError, match="CHK_USERS_EMAIL_NORMALISED"),
            conn.cursor() as cur,
        ):
            cur.execute(
                "UPDATE USERS SET EMAIL = ? WHERE ID = ?", (email, user.id.bytes)
            )
        conn.rollback()


def test_database_rejects_unknown_role(db_app: Flask, user: User):
    with db_app.app_context():
        conn = get_db()
        with (
            pytest.raises(DatabaseError, match="CHK_USERS_ROLE"),
            conn.cursor() as cur,
        ):
            cur.execute(
                "UPDATE USERS SET ROLE = 'SUPERUSER' WHERE ID = ?", (user.id.bytes,)
            )
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


def _admin(db_app: Flask, email: str, password: str) -> User:
    with db_app.app_context():
        return create_user(get_db(), email, password, role=Role.ADMIN)


def _is_admin(db_app: Flask, email: str) -> bool:
    with db_app.app_context():
        found = find_by_email(get_db(), email)
    return found is not None and found.is_admin


def test_last_admin_cannot_be_deleted(db_app: Flask, password: str):
    admin = _admin(db_app, "boss@example.com", password)

    with db_app.app_context(), pytest.raises(LastAdminError):
        delete_user(get_db(), admin)

    assert _is_admin(db_app, admin.email)


def test_last_admin_cannot_be_demoted(db_app: Flask, password: str):
    admin = _admin(db_app, "boss@example.com", password)

    with db_app.app_context(), pytest.raises(LastAdminError):
        set_role(get_db(), admin, Role.REGULAR)
    with db_app.app_context(), pytest.raises(LastAdminError):
        update_user(
            get_db(),
            admin,
            name=None,
            surname=None,
            role=Role.REGULAR,
            locked=False,
            password=None,
        )

    assert _is_admin(db_app, admin.email)


def test_admin_can_be_deleted_or_demoted_while_another_remains(
    db_app: Flask, password: str
):
    _admin(db_app, "boss@example.com", password)
    second = _admin(db_app, "second@example.com", password)
    third = _admin(db_app, "third@example.com", password)

    with db_app.app_context():
        conn = get_db()
        set_role(conn, second, Role.REGULAR)
        delete_user(conn, third)
        assert find_by_email(conn, third.email) is None

    assert not _is_admin(db_app, second.email)


def test_admins_removing_each_other_at_once_leave_one(
    db_app: Flask, other_conn: Connection, password: str
):
    first = _admin(db_app, "first@example.com", password)
    second = _admin(db_app, "second@example.com", password)
    errors: list[Exception] = []

    def delete_second() -> None:
        with db_app.app_context():
            try:
                delete_user(get_db(), second)
            except (DatabaseError, LastAdminError) as e:
                errors.append(e)

    # Another request demotes the first admin, not yet committed...
    with other_conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET ROLE = ? WHERE ID = ?",
            (Role.REGULAR.value, first.id.bytes),
        )
    # ...while this one deletes the second.
    thread = threading.Thread(target=delete_second)
    thread.start()
    time.sleep(0.5)
    other_conn.commit()
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert errors
    assert _is_admin(db_app, second.email)


def test_admin_exists(db_app: Flask, user: User, password: str):
    with db_app.app_context():
        conn = get_db()
        assert not admin_exists(conn)

        create_user(conn, "boss@example.com", password, role=Role.ADMIN)
        assert admin_exists(conn)


def test_set_role(db_app: Flask, user: User):
    with db_app.app_context():
        conn = get_db()
        set_role(conn, user, Role.ADMIN)

        updated = find_by_email(conn, user.email)
    assert updated is not None
    assert updated.role is Role.ADMIN


def test_create_user_rejects_duplicate_email(db_app: Flask, user: User):
    with (
        db_app.app_context(),
        pytest.raises(UserExistsError, match="reader@example.com"),
    ):
        create_user(get_db(), user.email.upper(), "another password")


def test_create_user_rejects_short_password(db_app: Flask):
    with db_app.app_context(), pytest.raises(PasswordTooShortError):
        create_user(get_db(), "short@example.com", "x" * (MIN_PASSWORD_LENGTH - 1))

    with db_app.app_context():
        assert find_by_email(get_db(), "short@example.com") is None


def test_create_user_accepts_minimum_length_password(db_app: Flask):
    with db_app.app_context():
        create_user(get_db(), "min@example.com", "x" * MIN_PASSWORD_LENGTH)


def test_set_password_rejects_short_password(db_app: Flask, user: User, password: str):
    with db_app.app_context(), pytest.raises(PasswordTooShortError):
        set_password(get_db(), user, "short")

    with db_app.app_context():
        assert authenticate(get_db(), user.email, password) is not None


def test_create_user_uses_given_min_length(db_app: Flask):
    with db_app.app_context(), pytest.raises(PasswordTooShortError, match="16"):
        create_user(get_db(), "short@example.com", "x" * 15, min_password_length=16)


def test_set_password_uses_given_min_length(db_app: Flask, user: User):
    with db_app.app_context(), pytest.raises(PasswordTooShortError, match="16"):
        set_password(get_db(), user, "x" * 15, min_password_length=16)


def test_update_user_applies_all_changes(db_app: Flask, user: User):
    with db_app.app_context():
        conn = get_db()
        update_user(
            conn,
            user,
            name="Ada",
            surname="Lovelace",
            role=Role.ADMIN,
            locked=True,
            password="x" * 12,
        )
        updated = find_by_email(conn, user.email)
    assert updated is not None
    assert (updated.name, updated.surname) == ("Ada", "Lovelace")
    assert updated.role is Role.ADMIN
    assert updated.is_locked
    assert updated.password_hash != user.password_hash


def test_update_user_without_password_keeps_it(db_app: Flask, user: User):
    with db_app.app_context():
        conn = get_db()
        update_user(
            conn,
            user,
            name=None,
            surname=None,
            role=Role.REGULAR,
            locked=True,
            password=None,
        )
        updated = find_by_email(conn, user.email)
    assert updated is not None
    assert updated.password_hash == user.password_hash


def test_update_user_rejects_short_password_and_changes_nothing(
    db_app: Flask, user: User
):
    with db_app.app_context(), pytest.raises(PasswordTooShortError, match="16"):
        update_user(
            get_db(),
            user,
            name="Ada",
            surname=None,
            role=Role.ADMIN,
            locked=True,
            password="x" * 15,
            min_password_length=16,
        )

    with db_app.app_context():
        unchanged = find_by_email(get_db(), user.email)
    assert unchanged is not None
    assert unchanged.name is None
    assert unchanged.role is Role.REGULAR
    assert not unchanged.is_locked


# A user's own account


def test_update_account_sets_name_surname_and_password(db_app: Flask, user: User):
    with db_app.app_context():
        conn = get_db()
        update_account(
            conn, user, name="Ada", surname="Lovelace", password="a-new-password"
        )
        updated = find_by_email(conn, user.email)
        assert authenticate(conn, user.email, "a-new-password") is not None
    assert updated is not None
    assert (updated.name, updated.surname) == ("Ada", "Lovelace")


def test_update_account_without_password_keeps_it(
    db_app: Flask, user: User, password: str
):
    with db_app.app_context():
        conn = get_db()
        update_account(conn, user, name=None, surname="Homer", password=None)
        assert authenticate(conn, user.email, password) is not None


def test_update_account_keeps_role_and_lock(
    db_app: Flask, user: User, lock_user: Callable[[User], None]
):
    """An account saved while an admin locks it stays locked."""
    lock_user(user)
    with db_app.app_context():
        conn = get_db()
        update_account(conn, user, name="Ada", surname=None, password=None)
        updated = find_by_email(conn, user.email)
    assert updated is not None
    assert updated.is_locked
    assert updated.role is Role.REGULAR


def test_update_account_rejects_short_password_and_changes_nothing(
    db_app: Flask, user: User
):
    with db_app.app_context(), pytest.raises(PasswordTooShortError, match="16"):
        update_account(
            get_db(),
            user,
            name="Ada",
            surname=None,
            password="x" * 15,
            min_password_length=16,
        )

    with db_app.app_context():
        unchanged = find_by_email(get_db(), user.email)
    assert unchanged is not None
    assert unchanged.name is None


@pytest.mark.parametrize(
    "session_id", ["", "not-a-uuid", "not-a-uuid:0123456789abcdef"]
)
def test_malformed_session_id_finds_nobody(db_app: Flask, user: User, session_id: str):
    with db_app.app_context():
        assert find_by_session_id(get_db(), session_id) is None


def test_refused_login_removal_retries_after_a_conflict(
    db_app: Flask,
    user: User,
    other_conn: Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """
    Another refused attempt removing the same, identical-looking row makes
    the removal fail with an update conflict: it tries again and succeeds.
    """
    remove = login_limits._delete_one_failure
    calls: list[datetime] = []

    def remove_after_another_request(
        conn: Connection, email: str, ip: str, attempted_at: datetime
    ) -> None:
        if not calls:
            # The other request removes the row first, committing a moment later.
            with other_conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM LOGIN_FAILURES "
                    "WHERE EMAIL = ? AND IP_ADDRESS = ? AND ATTEMPTED_AT = ? ROWS 1",
                    (email, ip, attempted_at),
                )
            threading.Timer(0.5, other_conn.commit).start()
        calls.append(attempted_at)
        remove(conn, email, ip, attempted_at)

    monkeypatch.setattr(
        login_limits, "_delete_one_failure", remove_after_another_request
    )
    with (
        db_app.app_context(),
        caplog.at_level(logging.DEBUG, logger="bookshelf.models.users"),
    ):
        conn = get_db()
        allowed = start_login_attempt(
            conn,
            user.email,
            "192.0.2.1",
            max_failures=0,
            max_ip_failures=10,
            window_minutes=15,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM LOGIN_FAILURES")
            (failures,) = cur.fetchone()

    assert not allowed
    assert len(calls) == 2
    assert failures == 0
    assert (
        f"Retrying removal of refused login for {user.email} from 192.0.2.1"
        in caplog.messages
    )


UPDATE_CONFLICT = 335544451
DEADLOCK = 335544336


def _database_error(code: int) -> DatabaseError:
    error = DatabaseError(f"error {code}")
    error.gds_codes = (code,)
    return error


@pytest.mark.parametrize(
    ("code", "tries"),
    [(UPDATE_CONFLICT, 10), (DEADLOCK, 1)],
    ids=["conflict-every-time", "other-error"],
)
def test_removing_a_refused_attempt_gives_up(
    db_app: Flask,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    tries: int,
):
    """
    Update conflicts are tried again, up to 10 times in all; any other error
    is not. Either way the last error reaches the caller.
    """
    calls: list[datetime] = []

    def fail(conn: Connection, email: str, ip: str, attempted_at: datetime) -> None:
        calls.append(attempted_at)
        conn.begin()  # as the failed DELETE did
        raise _database_error(code)

    monkeypatch.setattr(login_limits, "_delete_one_failure", fail)
    with db_app.app_context(), pytest.raises(DatabaseError, match=f"error {code}"):
        start_login_attempt(
            get_db(),
            user.email,
            "192.0.2.1",
            max_failures=0,
            max_ip_failures=10,
            window_minutes=15,
        )

    assert len(calls) == tries


def test_create_user_passes_on_unexpected_database_errors(
    db_app: Flask, fail_statement: Callable[[str], None]
):
    """Not taken for an existing email; no user is created."""
    fail_statement("INSERT INTO USERS")

    with db_app.app_context(), pytest.raises(DatabaseError, match="deadlock"):
        create_user(get_db(), "new@example.com", "a-long-enough-password")

    with db_app.app_context():
        assert find_by_email(get_db(), "new@example.com") is None


def _google_user(db_app: Flask, email: str = "g@example.com") -> User:
    with db_app.app_context():
        return create_user(get_db(), email, None, auth_method=AuthMethod.GOOGLE)


def test_users_log_in_locally_by_default(user: User):
    assert user.auth_method is AuthMethod.LOCAL


def test_google_user_has_no_password(db_app: Flask):
    user = _google_user(db_app, "G@Example.com")

    assert (user.email, user.auth_method) == ("g@example.com", AuthMethod.GOOGLE)
    assert user.password_hash is None
    assert user.google_subject is None


def test_google_user_cannot_be_given_a_password(db_app: Flask, password: str):
    with db_app.app_context(), pytest.raises(NoPasswordError):
        create_user(get_db(), "g@example.com", password, auth_method=AuthMethod.GOOGLE)


def test_local_user_needs_a_password(db_app: Flask):
    with db_app.app_context(), pytest.raises(PasswordTooShortError):
        create_user(get_db(), "l@example.com", None)


def test_google_user_cannot_log_in_with_a_password(db_app: Flask):
    _google_user(db_app)

    with db_app.app_context():
        assert authenticate(get_db(), "g@example.com", "") is None
        assert authenticate(get_db(), "g@example.com", "anything at all") is None


def test_first_google_login_binds_the_account_by_email(db_app: Flask):
    created = _google_user(db_app)

    with db_app.app_context():
        user = google_login(get_db(), "sub-1", "G@example.com")

    assert user is not None
    assert (user.id, user.google_subject) == (created.id, "sub-1")


def test_later_google_logins_use_the_bound_account(db_app: Flask):
    created = _google_user(db_app)
    with db_app.app_context():
        conn = get_db()
        google_login(conn, "sub-1", "g@example.com")

        # Its Gmail address changed: still the same Google account.
        user = google_login(conn, "sub-1", "renamed@example.com")

    assert user is not None and user.id == created.id


def test_another_google_account_with_the_email_is_refused(db_app: Flask):
    _google_user(db_app)
    with db_app.app_context():
        conn = get_db()
        google_login(conn, "sub-1", "g@example.com")

        assert google_login(conn, "sub-2", "g@example.com") is None


@pytest.mark.parametrize("email", ["reader@example.com", "nobody@example.com"])
def test_google_login_needs_a_google_user(db_app: Flask, user: User, email: str):
    """A local user, or no user at all, with the Google account's email."""
    with db_app.app_context():
        assert google_login(get_db(), "sub-1", email) is None
        assert find_by_id(get_db(), user.id) == user  # not bound


def test_switching_to_google_drops_the_password_and_ends_sessions(
    db_app: Flask, user: User
):
    with db_app.app_context():
        conn = get_db()
        set_auth_method(conn, user, AuthMethod.GOOGLE)
        switched = find_by_id(conn, user.id)

        assert switched is not None
        assert (switched.auth_method, switched.password_hash) == (
            AuthMethod.GOOGLE,
            None,
        )
        assert find_by_session_id(conn, user.get_id()) is None


def test_switching_to_local_sets_a_password_and_unbinds(db_app: Flask, password: str):
    google = _google_user(db_app)
    with db_app.app_context():
        conn = get_db()
        bound = google_login(conn, "sub-1", "g@example.com")
        assert bound is not None

        set_auth_method(conn, bound, AuthMethod.LOCAL, password=password)

        assert authenticate(conn, google.email, password) is not None
        local = find_by_id(conn, google.id)
        assert local is not None and local.google_subject is None
        assert find_by_session_id(conn, bound.get_id()) is None


def test_switching_to_local_needs_a_password(db_app: Flask):
    google = _google_user(db_app)

    with db_app.app_context(), pytest.raises(PasswordTooShortError):
        set_auth_method(get_db(), google, AuthMethod.LOCAL)


def test_a_google_user_has_no_password_to_set(db_app: Flask, password: str):
    google = _google_user(db_app)

    with db_app.app_context(), pytest.raises(NoPasswordError):
        set_password(get_db(), google, password)


def _admin_update(
    conn: Connection, user: User, auth_method: AuthMethod, password: str | None
) -> None:
    update_user(
        conn,
        user,
        name=None,
        surname=None,
        role=Role.REGULAR,
        locked=False,
        auth_method=auth_method,
        password=password,
    )


def test_admin_update_switches_the_auth_method(db_app: Flask, user: User, password):
    with db_app.app_context():
        conn = get_db()
        _admin_update(conn, user, AuthMethod.GOOGLE, None)
        google = find_by_id(conn, user.id)
        assert google is not None and google.password_hash is None

        with pytest.raises(PasswordTooShortError):
            _admin_update(conn, google, AuthMethod.LOCAL, None)
        _admin_update(conn, google, AuthMethod.LOCAL, password)
        assert authenticate(conn, user.email, password) is not None


def test_admin_update_keeps_a_google_users_binding(db_app: Flask):
    google = _google_user(db_app)
    with db_app.app_context():
        conn = get_db()
        bound = google_login(conn, "sub-1", "g@example.com")
        assert bound is not None

        update_user(
            conn,
            bound,
            name="Gina",
            surname=None,
            role=Role.REGULAR,
            locked=False,
            auth_method=AuthMethod.GOOGLE,
            password=None,
        )

        saved = find_by_id(conn, google.id)
        assert saved is not None
        assert (saved.name, saved.google_subject) == ("Gina", "sub-1")


def test_a_google_user_has_no_password_to_change(db_app: Flask, password: str):
    google = _google_user(db_app)

    with db_app.app_context(), pytest.raises(NoPasswordError):
        update_account(get_db(), google, name="Gina", surname=None, password=password)
