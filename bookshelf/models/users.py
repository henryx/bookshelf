import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from firebird.driver import Connection, DatabaseError

from bookshelf.config import MIN_PASSWORD_LENGTH

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "PasswordTooShortError",
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
    "list_users",
    "prune_login_failures",
    "set_locked",
    "set_password",
    "set_role",
    "start_login_attempt",
    "update_account",
    "update_user",
    "validate_new_password",
]

# Firebird GDS code for "violation of PRIMARY or UNIQUE KEY constraint".
_UNIQUE_KEY_VIOLATION = 335544665
# Firebird GDS code for "update conflicts with concurrent update".
_UPDATE_CONFLICT = 335544451
# Concurrent refused attempts that may each have to retry removing their row.
_REMOVE_ATTEMPT_TRIES = 10

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()
# Verified against unknown emails so a failed login takes the same time either way.
_DUMMY_HASH = _hasher.hash("dummy password for timing")


class Role(StrEnum):
    """Values allowed by the CHK_USERS_ROLE constraint on USERS.ROLE."""

    ADMIN = "ADMIN"
    REGULAR = "REGULAR"


class PasswordTooShortError(ValueError):
    """A new password is shorter than the required minimum."""

    def __init__(self, min_length: int) -> None:
        super().__init__(f"Password must be at least {min_length} characters long")
        self.min_length = min_length


def validate_new_password(
    password: str, *, min_length: int = MIN_PASSWORD_LENGTH
) -> None:
    """
    Raise PasswordTooShortError unless the password may be set. Only length is
    checked; existing passwords keep working even if shorter. Callers pass
    the configured length; the default is the lowest allowed.
    """
    if len(password) < min_length:
        raise PasswordTooShortError(min_length)


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
    name: str | None = None
    surname: str | None = None

    # Flask-Login user protocol
    is_authenticated = True
    is_anonymous = False

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_active(self) -> bool:
        return not self.is_locked

    @property
    def full_name(self) -> str | None:
        """Name and surname, either of which may be missing; None without both."""
        return " ".join(p for p in (self.name, self.surname) if p) or None

    @property
    def initials(self) -> str:
        """Initials of the name and surname, or the email's first letter."""
        parts = [p for p in (self.name, self.surname) if p] or [self.email]
        return "".join(p[0] for p in parts).upper()

    def get_id(self) -> str:
        """
        Session ID: the user ID plus a fingerprint of the password hash, so
        changing the password invalidates every open session and remember cookie.
        """
        fingerprint = hashlib.sha256(self.password_hash.encode()).hexdigest()[:16]
        return f"{self.id}:{fingerprint}"


def _normalise(email: str) -> str:
    return email.strip().lower()


type _UserRow = tuple[bytes, str, str, str, bool, str | None, str | None]


def _row_to_user(row: _UserRow) -> User:
    user_id, email, password_hash, role, is_locked, name, surname = row
    return User(
        id=UUID(bytes=user_id),
        email=email,
        password_hash=password_hash,
        role=Role(role),
        is_locked=is_locked,
        name=name,
        surname=surname,
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
        "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME "
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
    return _fetch_user(
        conn,
        "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME "
        "FROM USERS WHERE EMAIL = ?",
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
            "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME "
            "FROM USERS ORDER BY EMAIL"
        )
        return [_row_to_user(row) for row in cur.fetchall()]


def create_user(
    conn: Connection,
    email: str,
    password: str,
    *,
    role: Role = Role.REGULAR,
    name: str | None = None,
    surname: str | None = None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> User:
    """
    Raises UserExistsError if the email is taken, even by a user created
    after the caller checked, and PasswordTooShortError for a short password.
    Name and surname are optional (None), trimmed and non-blank when given.
    """
    validate_new_password(password, min_length=min_password_length)
    email = _normalise(email)
    with conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH, ROLE, NAME, SURNAME) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "RETURNING ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME",
                (
                    uuid4().bytes,
                    email,
                    _hasher.hash(password),
                    role.value,
                    name,
                    surname,
                ),
            )
        except DatabaseError as e:
            if _UNIQUE_KEY_VIOLATION in e.gds_codes:
                raise UserExistsError(email) from e
            raise
        row = cur.fetchone()
    conn.commit()
    return _row_to_user(row)


def set_password(
    conn: Connection,
    user: User,
    password: str,
    *,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """Raises PasswordTooShortError for a short password."""
    validate_new_password(password, min_length=min_password_length)
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


def update_user(
    conn: Connection,
    user: User,
    *,
    name: str | None,
    surname: str | None,
    role: Role,
    locked: bool,
    password: str | None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    An admin's changes: set name, surname, role, lock state and, unless
    password is None, a new password, all in one transaction. Raises
    PasswordTooShortError before changing anything.
    """
    if password is None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE USERS SET NAME = ?, SURNAME = ?, ROLE = ?, IS_LOCKED = ? "
                "WHERE ID = ?",
                (name, surname, role.value, locked, user.id.bytes),
            )
        conn.commit()
        return

    validate_new_password(password, min_length=min_password_length)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET NAME = ?, SURNAME = ?, ROLE = ?, IS_LOCKED = ?, "
            "PASSWORD_HASH = ? WHERE ID = ?",
            (name, surname, role.value, locked, _hasher.hash(password), user.id.bytes),
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
    PasswordTooShortError before changing anything.
    """
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
            (name, surname, _hasher.hash(password), user.id.bytes),
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


def start_login_attempt(
    conn: Connection,
    email: str,
    ip: str,
    *,
    max_failures: int,
    max_ip_failures: int,
    window_minutes: int,
) -> bool:
    """
    Record a login attempt as a failure before its password is checked, and
    return whether it may go ahead. It may not if, counting itself, the email
    has more than max_failures failures from this IP in the last
    window_minutes, or the IP more than max_ip_failures across all emails;
    a refused attempt is removed again, so it does not extend the block.

    Recording first, in its own transaction, means attempts sent at once each
    count the others, so no more than the limit reach the password check.
    A successful login then clears the failures (clear_login_failures).

    Counting per email and IP means failures from an attacker's IP do not
    lock the real user out. Unknown emails are limited too, so a block does
    not reveal which accounts exist. The email's failures from this IP older
    than window_minutes are pruned; only those, so concurrent attempts for
    other emails or IPs never delete the same rows and conflict.

    The failure's ID is the email's user, or NULL for an unknown email.
    """
    email = _normalise(email)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM LOGIN_FAILURES WHERE EMAIL = ? AND IP_ADDRESS = ? "
            "AND ATTEMPTED_AT <= DATEADD(? MINUTE TO CURRENT_TIMESTAMP)",
            (email, ip, -window_minutes),
        )
        cur.execute(
            "INSERT INTO LOGIN_FAILURES (ID, EMAIL, IP_ADDRESS) "
            "VALUES ((SELECT ID FROM USERS WHERE EMAIL = ?), ?, ?) "
            "RETURNING ATTEMPTED_AT",
            (email, email, ip),
        )
        (attempted_at,) = cur.fetchone()
    conn.commit()

    if not _over_limit(
        conn,
        email,
        ip,
        max_failures=max_failures,
        max_ip_failures=max_ip_failures,
        window_minutes=window_minutes,
    ):
        return True
    _remove_attempt(conn, email, ip, attempted_at)
    return False


def _remove_attempt(
    conn: Connection, email: str, ip: str, attempted_at: datetime
) -> None:
    """
    Delete the row a refused attempt added. Rows have no key of their own, but
    any row with the same email, IP and time is indistinguishable from it, so
    one of them is deleted. Concurrent refused attempts may pick the same row:
    Firebird then fails all but one with an update conflict, and the others
    try again, now seeing that row gone.
    """
    for _ in range(_REMOVE_ATTEMPT_TRIES - 1):
        try:
            _delete_one_failure(conn, email, ip, attempted_at)
            return
        except DatabaseError as e:
            if _UPDATE_CONFLICT not in e.gds_codes:
                raise
            conn.rollback()
            logger.debug("Retrying removal of refused login for %s from %s", email, ip)
    _delete_one_failure(conn, email, ip, attempted_at)


def _delete_one_failure(
    conn: Connection, email: str, ip: str, attempted_at: datetime
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM LOGIN_FAILURES "
            "WHERE EMAIL = ? AND IP_ADDRESS = ? AND ATTEMPTED_AT = ? ROWS 1",
            (email, ip, attempted_at),
        )
    conn.commit()


def _over_limit(
    conn: Connection,
    email: str,
    ip: str,
    *,
    max_failures: int,
    max_ip_failures: int,
    window_minutes: int,
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM LOGIN_FAILURES WHERE EMAIL = ? AND IP_ADDRESS = ? "
            "AND ATTEMPTED_AT > DATEADD(? MINUTE TO CURRENT_TIMESTAMP)",
            (email, ip, -window_minutes),
        )
        (email_failures,) = cur.fetchone()
        if email_failures > max_failures:
            return True
        cur.execute(
            "SELECT COUNT(*) FROM LOGIN_FAILURES WHERE IP_ADDRESS = ? "
            "AND ATTEMPTED_AT > DATEADD(? MINUTE TO CURRENT_TIMESTAMP)",
            (ip, -window_minutes),
        )
        (ip_failures,) = cur.fetchone()
    return ip_failures > max_ip_failures


def prune_login_failures(conn: Connection, *, window_minutes: int) -> int:
    """
    Delete every failure older than window_minutes, which no longer counts
    towards a block, and return how many were deleted. Logins prune only
    their own email and IP, so this clears the rest (run it periodically).
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM LOGIN_FAILURES "
            "WHERE ATTEMPTED_AT <= DATEADD(? MINUTE TO CURRENT_TIMESTAMP)",
            (-window_minutes,),
        )
        deleted = cur.affected_rows
    conn.commit()
    return deleted


def clear_login_failures(conn: Connection, email: str, ip: str) -> None:
    """Forget the email's failures from this IP, after it logged in from there."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM LOGIN_FAILURES WHERE EMAIL = ? AND IP_ADDRESS = ?",
            (_normalise(email), ip),
        )
    conn.commit()
