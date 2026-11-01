from dataclasses import dataclass
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from firebird.driver import Connection

__all__ = [
    "Role",
    "User",
    "authenticate",
    "create_user",
    "find_by_email",
    "find_by_id",
    "set_password",
]

_hasher = PasswordHasher()
# Verified against unknown emails so a failed login takes the same time either way.
_DUMMY_HASH = _hasher.hash("dummy password for timing")

_COLUMNS = "ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED"


class Role(StrEnum):
    """Values allowed by the CHK_USERS_ROLE constraint on USERS.ROLE."""

    ADMIN = "ADMIN"
    REGULAR = "REGULAR"


@dataclass(frozen=True, kw_only=True)
class User:
    id: int
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
        return str(self.id)


def _normalise(email: str) -> str:
    return email.strip().lower()


def _row_to_user(row: tuple[int, str, str, str, bool]) -> User:
    user_id, email, password_hash, role, is_locked = row
    return User(
        id=user_id,
        email=email,
        password_hash=password_hash,
        role=Role(role),
        is_locked=is_locked,
    )


def _find_one(conn: Connection, where: str, value: int | str) -> User | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM USERS WHERE {where} = ?", (value,))
        row = cur.fetchone()
    return _row_to_user(row) if row else None


def find_by_id(conn: Connection, user_id: int) -> User | None:
    return _find_one(conn, "ID", user_id)


def find_by_email(conn: Connection, email: str) -> User | None:
    return _find_one(conn, "EMAIL", _normalise(email))


def create_user(
    conn: Connection, email: str, password: str, *, role: Role = Role.REGULAR
) -> User:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO USERS (EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?) "
            f"RETURNING {_COLUMNS}",
            (_normalise(email), _hasher.hash(password), role.value),
        )
        row = cur.fetchone()
    conn.commit()
    return _row_to_user(row)


def set_password(conn: Connection, user: User, password: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE USERS SET PASSWORD_HASH = ? WHERE ID = ?",
            (_hasher.hash(password), user.id),
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
