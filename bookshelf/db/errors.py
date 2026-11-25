"""Telling apart the database errors the models handle, on either backend."""

from firebird.driver import DatabaseError
from sqlalchemy.exc import DBAPIError

__all__ = [
    "is_concurrent_delete",
    "is_foreign_key_violation",
    "is_unique_violation",
    "is_update_conflict",
]

# The database errors the models handle: Firebird's GDS code and
# PostgreSQL's SQLSTATE for each.
_UNIQUE_KEY_VIOLATION = (335544665, "23505")
_FOREIGN_KEY_VIOLATION = (335544466, "23503")
# Another transaction changed or deleted the row meanwhile: Firebird's lock
# conflict, PostgreSQL's serialization failure.
_UPDATE_CONFLICT = (335544451, "40001")
# A row the statement changes was deleted by a transaction it waited for,
# which then committed: Firebird's "cannot update erased record". PostgreSQL
# raises it only in a repeatable read transaction, as a serialization
# failure; read committed, it skips the row.
_CONCURRENT_DELETE = (335544382, "40001")


def _has_error(error: BaseException, codes: tuple[int, str]) -> bool:
    """True when `error` is the database error these codes name (see above)."""
    gds_code, sqlstate = codes
    orig = error.orig if isinstance(error, DBAPIError) else error
    if isinstance(orig, DatabaseError):
        return gds_code in orig.gds_codes
    return getattr(orig, "sqlstate", None) == sqlstate


def is_unique_violation(error: BaseException) -> bool:
    return _has_error(error, _UNIQUE_KEY_VIOLATION)


def is_foreign_key_violation(error: BaseException) -> bool:
    return _has_error(error, _FOREIGN_KEY_VIOLATION)


def is_update_conflict(error: BaseException) -> bool:
    return _has_error(error, _UPDATE_CONFLICT)


def is_concurrent_delete(error: BaseException) -> bool:
    return _has_error(error, _CONCURRENT_DELETE)
