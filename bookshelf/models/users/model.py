"""Users' data types and errors, the new-password rule and email normalising."""

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from bookshelf.config import MIN_PASSWORD_LENGTH

# Firebird GDS code for "violation of PRIMARY or UNIQUE KEY constraint".
UNIQUE_KEY_VIOLATION = 335544665


class Role(StrEnum):
    """Values allowed by the CHK_USERS_ROLE constraint on USERS.ROLE."""

    ADMIN = "ADMIN"
    REGULAR = "REGULAR"


class AuthMethod(StrEnum):
    """How a user logs in: CHK_USERS_AUTH_METHOD's values for USERS.AUTH_METHOD."""

    LOCAL = "LOCAL"  # with their password
    GOOGLE = "GOOGLE"  # with their Google account; no password


class NoPasswordError(ValueError):
    """A password given for a user who logs in with Google, and has none."""

    def __init__(self, email: str) -> None:
        super().__init__(f"{email} logs in with Google and has no password")
        self.email = email


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


class LastAdminError(Exception):
    """Removing or demoting this admin would leave no admin."""

    def __init__(self, email: str) -> None:
        super().__init__(f"{email} is the only admin left")
        self.email = email


class UserExistsError(Exception):
    """A user with this email already exists."""

    def __init__(self, email: str) -> None:
        super().__init__(f"User {email} already exists")
        self.email = email


@dataclass(frozen=True, kw_only=True)
class User:
    id: UUID
    email: str
    # None for a Google user; google_subject is their Google account, once bound.
    password_hash: str | None
    role: Role
    is_locked: bool
    name: str | None = None
    surname: str | None = None
    auth_method: AuthMethod = AuthMethod.LOCAL
    google_subject: str | None = None

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
        Session ID: the user ID plus a fingerprint of how they log in (the
        password hash, or the Google account), so changing the password or
        the way of logging in invalidates every open session and remember
        cookie.
        """
        secret = self.password_hash or f"google:{self.google_subject}"
        fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:16]
        return f"{self.id}:{fingerprint}"


def normalise_email(email: str) -> str:
    return email.strip().lower()
