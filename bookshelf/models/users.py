import hashlib
import hmac
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from firebird.driver import Connection, DatabaseError

__all__ = [
    "Role",
    "User",
    "UserExistsError",
    "admin_exists",
    "authenticate",
    "clear_login_failures",
    "create_user",
    "delete_user",
    "find_by_email",
    "find_by_id",
    "find_by_session_id",
    "is_login_blocked",
    "list_users",
    "record_login_failure",
    "set_locked",
    "set_password",
    "set_role",
]

# Firebird GDS code for "violation of PRIMARY or UNIQUE KEY constraint".
_UNIQUE_KEY_VIOLATION = 335544665

_hasher = PasswordHasher()
# Verified against unknown emails so a failed login takes the same time either way.
_DUMMY_HASH = _hasher.hash("dummy password for timing")


class Role(StrEnum):
    """Values allowed by the CHK_USERS_ROLE constraint on USERS.ROLE."""

    ADMIN = "ADMIN"
    REGULAR = "REGULAR"


class UserExistsError(Exception):
    """A user with this email already exists."""

    def __init__(self, email: str) -> None:
        super().__init__(f"User {email} already exists")
        self.email = email


@dataclass(frozen=True, kw_only=True)
class User:
    id: UUID
    email: str
    password_hash: str
    role: Role
    is_locked: bool

    # Flask-Login user protocol
    is_authenticated = True
    is_anonymous = False

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_active(self) -> bool:
        return not self.is_locked

    def get_id(self) -> str:
        """
        Session ID: the user ID plus a fingerprint of the password hash, so
        changing the password invalidates every open session and remember cookie.
        """
        fingerprint = hashlib.sha256(self.password_hash.encode()).hexdigest()[:16]
        return f"{self.id}:{fingerprint}"


def _normalise(email: str) -> str:
    return email.strip().lower()


def _row_to_user(row: tuple[bytes, str, str, str, bool]) -> User:
    user_id, email, password_hash, role, is_locked = row
    return User(
        id=UUID(bytes=user_id),
        email=email,
        password_hash=password_hash,
        role=Role(role),
        is_locked=is_locked,
    )


def _fetch_user(
    conn: Connection, query: str, params: tuple[bytes | str]
) -> User | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return _row_to_user(row) if row else None


def find_by_id(conn: Connection, user_id: UUID) -> User | None:
    return _fetch_user(
        conn,
        "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED FROM USERS WHERE ID = ?",
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
    return _fetch_user(
        conn,
        "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED FROM USERS WHERE EMAIL = ?",
        (_normalise(email),),
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
            "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED FROM USERS ORDER BY EMAIL"
        )
        return [_row_to_user(row) for row in cur.fetchall()]


def create_user(
    conn: Connection, email: str, password: str, *, role: Role = Role.REGULAR
) -> User:
    """
    Raises UserExistsError if the email is taken, even by a user created
    after the caller checked.
    """
    email = _normalise(email)
    with conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?) "
                "RETURNING ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED",
                (uuid4().bytes, email, _hasher.hash(password), role.value),
            )
        except DatabaseError as e:
            if _UNIQUE_KEY_VIOLATION in e.gds_codes:
                raise UserExistsError(email) from e
            raise
        row = cur.fetchone()
    conn.commit()
    return _row_to_user(row)


def set_password(conn: Connection, user: User, password: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET PASSWORD_HASH = ? WHERE ID = ?",
            (_hasher.hash(password), user.id.bytes),
        )
    conn.commit()


def delete_user(conn: Connection, user: User) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM USERS WHERE ID = ?", (user.id.bytes,))
    conn.commit()


def set_role(conn: Connection, user: User, role: Role) -> None:
    with conn.cursor() as cur:
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


def _password_matches(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def authenticate(conn: Connection, email: str, password: str) -> User | None:
    """
    Return the user if the credentials are valid and the account is not locked.
    """
    user = find_by_email(conn, email)
    if user is None:
        _password_matches(_DUMMY_HASH, password)
        return None
    if not _password_matches(user.password_hash, password):
        return None
    if user.is_locked:
        return None
    return user


def is_login_blocked(
    conn: Connection, email: str, *, max_failures: int, window_minutes: int
) -> bool:
    """
    True if the email had at least max_failures failed logins in the last
    window_minutes. Applies to unknown emails too, so a block does not reveal
    which accounts exist.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM LOGIN_FAILURES WHERE EMAIL = ? "
            "AND ATTEMPTED_AT > DATEADD(? MINUTE TO CURRENT_TIMESTAMP)",
            (_normalise(email), -window_minutes),
        )
        (failures,) = cur.fetchone()
    return failures >= max_failures


def record_login_failure(conn: Connection, email: str, *, window_minutes: int) -> None:
    """
    Record a failed login and prune failures older than window_minutes.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM LOGIN_FAILURES "
            "WHERE ATTEMPTED_AT <= DATEADD(? MINUTE TO CURRENT_TIMESTAMP)",
            (-window_minutes,),
        )
        cur.execute(
            "INSERT INTO LOGIN_FAILURES (ID, EMAIL) VALUES (?, ?)",
            (uuid4().bytes, _normalise(email)),
        )
    conn.commit()


def clear_login_failures(conn: Connection, email: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM LOGIN_FAILURES WHERE EMAIL = ?", (_normalise(email),))
    conn.commit()
