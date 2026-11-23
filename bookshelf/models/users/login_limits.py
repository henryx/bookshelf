"""Login limits: failed attempts per email and IP, and per IP."""

import logging
from datetime import datetime

from firebird.driver import Connection, DatabaseError

from bookshelf.models.users.model import normalise_email

# Firebird GDS code for "update conflicts with concurrent update".
_UPDATE_CONFLICT = 335544451
# Concurrent refused attempts that may each have to retry removing their row.
_REMOVE_ATTEMPT_TRIES = 10

logger = logging.getLogger(__name__)


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
    email = normalise_email(email)
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
            (normalise_email(email), ip),
        )
    conn.commit()
