"""Logging in: with a password, or with a Google account."""

import logging

from firebird.driver import Connection

from bookshelf.models.users.listing import fetch_user, find_by_email, find_by_id
from bookshelf.models.users.model import AuthMethod, User
from bookshelf.models.users.passwords import DUMMY_HASH, password_matches

logger = logging.getLogger(__name__)


def authenticate(conn: Connection, email: str, password: str) -> User | None:
    """
    Return the user if the credentials are valid and the account is not
    locked. A Google user never matches: they have no password.
    """
    user = find_by_email(conn, email)
    if user is None or user.password_hash is None:
        password_matches(DUMMY_HASH, password)
        return None
    if not password_matches(user.password_hash, password):
        return None
    if user.is_locked:
        return None
    return user


def google_login(conn: Connection, subject: str, email: str) -> User | None:
    """
    The user a Google account logs in as: the Google user bound to `subject`
    (Google's ID for the account), else the Google user with this email not
    yet bound to any account, which this binds to it. None otherwise: no
    such user, a local one, or one bound to another Google account. The
    caller makes sure Google verified the email. Locked users are returned:
    the caller refuses them.
    """
    user = fetch_user(
        conn,
        "SELECT ID, EMAIL, PASSWORD_HASH, ROLE, IS_LOCKED, NAME, SURNAME, "
        "AUTH_METHOD, GOOGLE_SUBJECT FROM USERS WHERE GOOGLE_SUBJECT = ?",
        (subject,),
    )
    if user is not None:
        return user
    user = find_by_email(conn, email)
    if user is None or user.auth_method is not AuthMethod.GOOGLE:
        return None
    with conn.cursor() as cur:
        # Only while unbound: another login may have bound it meanwhile.
        cur.execute(
            "UPDATE USERS SET GOOGLE_SUBJECT = ? "
            "WHERE ID = ? AND AUTH_METHOD = 'GOOGLE' AND GOOGLE_SUBJECT IS NULL",
            (subject, user.id.bytes),
        )
        bound = cur.affected_rows > 0
    conn.commit()
    if not bound:
        return None
    logger.info("User %s bound to their Google account", user.email)
    return find_by_id(conn, user.id)
