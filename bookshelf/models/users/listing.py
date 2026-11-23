"""Finding and listing users."""

import hmac
from uuid import UUID

from firebird.driver import Connection

from bookshelf.models.users.model import AuthMethod, Role, User, normalise_email

type UserRow = tuple[
    bytes, str, str | None, str, bool, str | None, str | None, str, str | None
]


def row_to_user(row: UserRow) -> User:
    (
        user_id,
        email,
        password_hash,
        role,
        is_locked,
        name,
        surname,
        auth_method,
        google_subject,
    ) = row
    return User(
        id=UUID(bytes=user_id),
        email=email,
        password_hash=password_hash,
        role=Role(role),
        is_locked=is_locked,
        name=name,
        surname=surname,
        auth_method=AuthMethod(auth_method),
        google_subject=google_subject,
    )


def fetch_user(conn: Connection, query: str, params: tuple[bytes | str]) -> User | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return row_to_user(row) if row else None


def find_by_id(conn: Connection, user_id: UUID) -> User | None:
    return fetch_user(
        conn,
        "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME, "
        "AUTH_METHOD, GOOGLE_SUBJECT "
        "FROM USERS WHERE ID = ?",
        (user_id.bytes,),
    )


def find_by_session_id(conn: Connection, session_id: str) -> User | None:
    """
    Return the user a session ID belongs to, or None if it is malformed or
    stale (the password changed since it was issued).
    """
    raw_id, _, _ = session_id.partition(":")
    try:
        user_id = UUID(raw_id)
    except ValueError:
        return None
    user = find_by_id(conn, user_id)
    if user is None or not hmac.compare_digest(user.get_id(), session_id):
        return None
    return user


def find_by_email(conn: Connection, email: str) -> User | None:
    return fetch_user(
        conn,
        "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME, "
        "AUTH_METHOD, GOOGLE_SUBJECT "
        "FROM USERS WHERE EMAIL = ?",
        (normalise_email(email),),
    )


def admin_exists(conn: Connection) -> bool:
    """
    True if any admin exists, locked or not.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT FIRST 1 1 FROM USERS WHERE ROLE = ?", (Role.ADMIN.value,))
        return cur.fetchone() is not None


def list_users(conn: Connection) -> list[User]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME, "
            "AUTH_METHOD, GOOGLE_SUBJECT "
            "FROM USERS ORDER BY EMAIL"
        )
        return [row_to_user(row) for row in cur.fetchall()]
