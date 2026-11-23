"""Creating, changing and deleting users."""

from uuid import uuid4

from firebird.driver import Connection, Cursor, DatabaseError

from bookshelf.models.users.listing import row_to_user
from bookshelf.models.users.model import (
    MIN_PASSWORD_LENGTH,
    UNIQUE_KEY_VIOLATION,
    AuthMethod,
    LastAdminError,
    NoPasswordError,
    Role,
    User,
    UserExistsError,
    normalise_email,
    validate_new_password,
)
from bookshelf.models.users.passwords import credentials, hash_password


def create_user(
    conn: Connection,
    email: str,
    password: str | None,
    *,
    role: Role = Role.REGULAR,
    name: str | None = None,
    surname: str | None = None,
    auth_method: AuthMethod = AuthMethod.LOCAL,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> User:
    """
    Raises UserExistsError if the email is taken, even by a user created
    after the caller checked. A local user needs a password
    (PasswordTooShortError when short or missing); a Google user has none
    (password None, else NoPasswordError) and is bound to their Google
    account on their first login (google_login). Name and surname are
    optional (None), trimmed and non-blank when given.
    """
    email = normalise_email(email)
    password_hash = credentials(email, auth_method, password, min_password_length)
    with conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO USERS "
                "(ID, EMAIL, PASSWORD_HASH, ROLE, NAME, SURNAME, AUTH_METHOD) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "RETURNING ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME, "
                "AUTH_METHOD, GOOGLE_SUBJECT",
                (
                    uuid4().bytes,
                    email,
                    password_hash,
                    role.value,
                    name,
                    surname,
                    auth_method.value,
                ),
            )
        except DatabaseError as e:
            if UNIQUE_KEY_VIOLATION in e.gds_codes:
                raise UserExistsError(email) from e
            raise
        row = cur.fetchone()
    conn.commit()
    return row_to_user(row)


def set_password(
    conn: Connection,
    user: User,
    password: str,
    *,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    Raises PasswordTooShortError for a short password, and NoPasswordError
    for a Google user (set_auth_method makes them local, with a password).
    """
    if user.auth_method is AuthMethod.GOOGLE:
        raise NoPasswordError(user.email)
    validate_new_password(password, min_length=min_password_length)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET PASSWORD_HASH = ? WHERE ID = ?",
            (hash_password(password), user.id.bytes),
        )
    conn.commit()


def set_auth_method(
    conn: Connection,
    user: User,
    auth_method: AuthMethod,
    *,
    password: str | None = None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    Make the user log in by auth_method, ending their open sessions. Google:
    no password (NoPasswordError if one is given), bound again on their next
    login. Local: with `password` (PasswordTooShortError when short or
    missing).
    """
    password_hash = credentials(user.email, auth_method, password, min_password_length)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET AUTH_METHOD = ?, PASSWORD_HASH = ?, GOOGLE_SUBJECT = NULL "
            "WHERE ID = ?",
            (auth_method.value, password_hash, user.id.bytes),
        )
    conn.commit()


def _keep_another_admin(conn: Connection, cur: Cursor, user: User) -> None:
    """
    Before `user` stops being an admin: raise LastAdminError (rolling back)
    unless another admin exists. The other admins' rows are updated, unchanged,
    so this transaction holds them: two admins removing each other at once
    then conflict, and one of them fails instead of both succeeding.
    Without an admin, the setup wizard would open to anyone.
    """
    cur.execute(
        "UPDATE USERS SET ROLE = ROLE WHERE ROLE = ? AND ID <> ?",
        (Role.ADMIN.value, user.id.bytes),
    )
    if cur.affected_rows == 0:
        conn.rollback()
        raise LastAdminError(user.email)


def delete_user(conn: Connection, user: User) -> None:
    """Raises LastAdminError if `user` is the only admin."""
    with conn.cursor() as cur:
        if user.is_admin:
            _keep_another_admin(conn, cur, user)
        cur.execute("DELETE FROM USERS WHERE ID = ?", (user.id.bytes,))
    conn.commit()


def set_role(conn: Connection, user: User, role: Role) -> None:
    """Raises LastAdminError when demoting the only admin."""
    with conn.cursor() as cur:
        if user.is_admin and role is not Role.ADMIN:
            _keep_another_admin(conn, cur, user)
        cur.execute(
            "UPDATE USERS SET ROLE = ? WHERE ID = ?", (role.value, user.id.bytes)
        )
    conn.commit()


def set_locked(conn: Connection, user: User, *, locked: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET IS_LOCKED = ? WHERE ID = ?", (locked, user.id.bytes)
        )
    conn.commit()


def update_user(
    conn: Connection,
    user: User,
    *,
    name: str | None,
    surname: str | None,
    role: Role,
    locked: bool,
    password: str | None,
    auth_method: AuthMethod | None = None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    An admin's changes, all in one transaction: name, surname, role, lock
    state and how the user logs in (auth_method; None keeps it). A local
    user gets a new password unless password is None; one switching from
    Google needs one. Switching to Google drops the password. Raises
    PasswordTooShortError or NoPasswordError before changing anything, and
    LastAdminError when demoting the only admin.
    """
    auth_method = auth_method or user.auth_method
    switching = auth_method is not user.auth_method
    new_credentials = switching or password is not None
    password_hash = (
        credentials(user.email, auth_method, password, min_password_length)
        if new_credentials
        else None
    )
    with conn.cursor() as cur:
        if user.is_admin and role is not Role.ADMIN:
            _keep_another_admin(conn, cur, user)
        cur.execute(
            "UPDATE USERS SET NAME = ?, SURNAME = ?, ROLE = ?, IS_LOCKED = ? "
            "WHERE ID = ?",
            (name, surname, role.value, locked, user.id.bytes),
        )
        if new_credentials:
            cur.execute(
                "UPDATE USERS SET AUTH_METHOD = ?, PASSWORD_HASH = ?, "
                "GOOGLE_SUBJECT = NULL WHERE ID = ?",
                (auth_method.value, password_hash, user.id.bytes),
            )
    conn.commit()


def update_account(
    conn: Connection,
    user: User,
    *,
    name: str | None,
    surname: str | None,
    password: str | None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    A user's changes to their own account: set name, surname and, unless
    password is None, a new password, in one transaction. Role and lock state
    are left alone, so an admin's lock made meanwhile stays. Raises
    PasswordTooShortError, or NoPasswordError for a Google user's password,
    before changing anything.
    """
    if password is not None and user.auth_method is AuthMethod.GOOGLE:
        raise NoPasswordError(user.email)
    if password is None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE USERS SET NAME = ?, SURNAME = ? WHERE ID = ?",
                (name, surname, user.id.bytes),
            )
        conn.commit()
        return

    validate_new_password(password, min_length=min_password_length)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET NAME = ?, SURNAME = ?, PASSWORD_HASH = ? WHERE ID = ?",
            (name, surname, hash_password(password), user.id.bytes),
        )
    conn.commit()
