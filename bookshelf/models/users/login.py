"""Logging in: with a password, or with a Google account."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.db import (
    execute_change,
    is_concurrent_delete,
    is_update_conflict,
    read_committed,
)
from bookshelf.db.orm import UserRow
from bookshelf.models.users.listing import fetch_user, find_by_email, find_by_id
from bookshelf.models.users.model import AuthMethod, User
from bookshelf.models.users.passwords import DUMMY_HASH, password_matches

logger = logging.getLogger(__name__)


def authenticate(session: Session, email: str, password: str) -> User | None:
    """
    Return the user if the credentials are valid and the account is not
    locked. A Google user never matches: they have no password.
    """
    user = find_by_email(session, email)
    if user is None or user.password_hash is None:
        password_matches(DUMMY_HASH, password)
        return None
    if not password_matches(user.password_hash, password):
        return None
    if user.is_locked:
        return None
    return user


def google_login(session: Session, subject: str, email: str) -> User | None:
    """
    The user a Google account logs in as: the Google user bound to `subject`
    (Google's ID for the account), else the Google user with this email not
    yet bound to any account, which this binds to it. None otherwise: no
    such user, a local one, or one bound to another Google account. The
    caller makes sure Google verified the email. Locked users are returned:
    the caller refuses them.
    """
    user = fetch_user(session, select(UserRow).where(UserRow.google_subject == subject))
    if user is not None:
        return user
    user = find_by_email(session, email)
    if user is None or user.auth_method is not AuthMethod.GOOGLE:
        return None
    # Only while unbound: another login may have bound it meanwhile.
    bound = (
        execute_change(
            session,
            update(UserRow)
            .where(
                UserRow.id == user.id,
                UserRow.auth_method == AuthMethod.GOOGLE.value,
                UserRow.google_subject.is_(None),
            )
            .values(google_subject=subject),
        ).rowcount
        > 0
    )
    session.commit()
    if not bound:
        return None
    logger.info("User %s bound to their Google account", user.email)
    return find_by_id(session, user.id)


def record_login(session: Session, user_id: UUID) -> None:
    """
    Set the user's last login to now, in UTC (the app's clock: Firebird 3 has
    no time zones). In a read committed transaction of its own, so a
    concurrent change of the user (another login, an admin's edit) is waited
    for rather than failing the login: Firebird then reports an update
    conflict, or a deleted user, and it runs once more, seeing that change.
    """
    try:
        _stamp_login(session, user_id)
    except DBAPIError as e:
        if not (is_update_conflict(e) or is_concurrent_delete(e)):
            raise
        logger.info("Recording login of %s again after a concurrent change", user_id)
        _stamp_login(session, user_id)


def _stamp_login(session: Session, user_id: UUID) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with read_committed(session):
        execute_change(
            session,
            update(UserRow).where(UserRow.id == user_id).values(last_login_at=now),
        )
        session.commit()
