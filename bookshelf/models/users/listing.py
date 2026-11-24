"""Finding and listing users."""

import hmac
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from bookshelf.db.orm import UserRow
from bookshelf.models.users.model import AuthMethod, Role, User, normalise_email


def row_to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        role=Role(row.role),
        is_locked=row.is_locked,
        name=row.name,
        surname=row.surname,
        auth_method=AuthMethod(row.auth_method),
        google_subject=row.google_subject,
    )


def fetch_user(session: Session, query: Select[UserRow]) -> User | None:
    row = session.scalars(query).one_or_none()
    return row_to_user(row) if row else None


def find_by_id(session: Session, user_id: UUID) -> User | None:
    return fetch_user(session, select(UserRow).where(UserRow.id == user_id))


def find_by_session_id(session: Session, session_id: str) -> User | None:
    """
    Return the user a session ID belongs to, or None if it is malformed or
    stale (the password changed since it was issued).
    """
    raw_id, _, _ = session_id.partition(":")
    try:
        user_id = UUID(raw_id)
    except ValueError:
        return None
    user = find_by_id(session, user_id)
    if user is None or not hmac.compare_digest(user.get_id(), session_id):
        return None
    return user


def find_by_email(session: Session, email: str) -> User | None:
    return fetch_user(
        session, select(UserRow).where(UserRow.email == normalise_email(email))
    )


def admin_exists(session: Session) -> bool:
    """
    True if any admin exists, locked or not.
    """
    query = select(UserRow.id).where(UserRow.role == Role.ADMIN.value).limit(1)
    return session.scalars(query).first() is not None


def list_users(session: Session) -> list[User]:
    return [
        row_to_user(row)
        for row in session.scalars(select(UserRow).order_by(UserRow.email))
    ]
