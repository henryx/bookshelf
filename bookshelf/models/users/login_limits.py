"""Login limits: failed attempts per email and IP, and per IP."""

import logging
from datetime import datetime

from sqlalchemy import ColumnElement, delete, func, insert, literal_column, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.db import UPDATE_CONFLICT, execute_change, has_gds_code
from bookshelf.db.orm import LoginFailureRow, UserRow
from bookshelf.models.users.model import normalise_email

# Concurrent refused attempts that may each have to retry removing their row.
_REMOVE_ATTEMPT_TRIES = 10

logger = logging.getLogger(__name__)


def start_login_attempt(
    session: Session,
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
    email = normalise_email(email)
    execute_change(
        session,
        delete(LoginFailureRow).where(
            LoginFailureRow.email == email,
            LoginFailureRow.ip_address == ip,
            LoginFailureRow.attempted_at <= _minutes_ago(window_minutes),
        ),
    )
    attempted_at = session.scalars(
        insert(LoginFailureRow)
        .values(
            id=select(UserRow.id).where(UserRow.email == email).scalar_subquery(),
            email=email,
            ip_address=ip,
        )
        .returning(LoginFailureRow.attempted_at)
    ).one()
    session.commit()

    if not _over_limit(
        session,
        email,
        ip,
        max_failures=max_failures,
        max_ip_failures=max_ip_failures,
        window_minutes=window_minutes,
    ):
        return True
    _remove_attempt(session, email, ip, attempted_at)
    return False


def _remove_attempt(
    session: Session, email: str, ip: str, attempted_at: datetime
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
            _delete_one_failure(session, email, ip, attempted_at)
            return
        except DBAPIError as e:
            if not has_gds_code(e, UPDATE_CONFLICT):
                raise
            session.rollback()
            logger.debug("Retrying removal of refused login for %s from %s", email, ip)
    _delete_one_failure(session, email, ip, attempted_at)


def _delete_one_failure(
    session: Session, email: str, ip: str, attempted_at: datetime
) -> None:
    # The table has no key: the row is picked by its record key, RDB$DB_KEY.
    # The subquery's unqualified RDB$DB_KEY is its own table's.
    record_key = literal_column("RDB$DB_KEY")
    first = (
        select(record_key)
        .select_from(LoginFailureRow)
        .where(
            LoginFailureRow.email == email,
            LoginFailureRow.ip_address == ip,
            LoginFailureRow.attempted_at == attempted_at,
        )
        .limit(1)
        .correlate(None)
        .scalar_subquery()
    )
    execute_change(session, delete(LoginFailureRow).where(record_key == first))
    session.commit()


def _over_limit(
    session: Session,
    email: str,
    ip: str,
    *,
    max_failures: int,
    max_ip_failures: int,
    window_minutes: int,
) -> bool:
    since = _minutes_ago(window_minutes)
    email_failures = (
        session.scalar(
            select(func.count()).where(
                LoginFailureRow.email == email,
                LoginFailureRow.ip_address == ip,
                LoginFailureRow.attempted_at > since,
            )
        )
        or 0
    )
    if email_failures > max_failures:
        return True
    ip_failures = (
        session.scalar(
            select(func.count()).where(
                LoginFailureRow.ip_address == ip, LoginFailureRow.attempted_at > since
            )
        )
        or 0
    )
    return ip_failures > max_ip_failures


def prune_login_failures(session: Session, *, window_minutes: int) -> int:
    """
    Delete every failure older than window_minutes, which no longer counts
    towards a block, and return how many were deleted. Logins prune only
    their own email and IP, so this clears the rest (run it periodically).
    """
    deleted = execute_change(
        session,
        delete(LoginFailureRow).where(
            LoginFailureRow.attempted_at <= _minutes_ago(window_minutes)
        ),
    ).rowcount
    session.commit()
    return deleted


def clear_login_failures(session: Session, email: str, ip: str) -> None:
    """Forget the email's failures from this IP, after it logged in from there."""
    execute_change(
        session,
        delete(LoginFailureRow).where(
            LoginFailureRow.email == normalise_email(email),
            LoginFailureRow.ip_address == ip,
        ),
    )
    session.commit()


def _minutes_ago(minutes: int) -> ColumnElement[datetime]:
    """The database's current time less `minutes` (its clock, as ATTEMPTED_AT's)."""
    return func.dateadd(literal_column("MINUTE"), -minutes, func.current_timestamp())
