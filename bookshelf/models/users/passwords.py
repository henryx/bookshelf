"""Hashing and checking passwords (Argon2), and the credentials a user needs."""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from bookshelf.models.users.model import (
    AuthMethod,
    NoPasswordError,
    validate_new_password,
)

_hasher = PasswordHasher()
# Verified against unknown emails so a failed login takes the same time either way.
DUMMY_HASH = _hasher.hash("dummy password for timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def password_matches(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def credentials(
    email: str, auth_method: AuthMethod, password: str | None, min_length: int
) -> str | None:
    """
    The password hash for a user logging in by auth_method: one of
    `password` for a local user (PasswordTooShortError when short or
    missing), None for a Google user (NoPasswordError if one is given).
    """
    if auth_method is AuthMethod.GOOGLE:
        if password is not None:
            raise NoPasswordError(email)
        return None
    validate_new_password(password or "", min_length=min_length)
    return hash_password(password or "")
