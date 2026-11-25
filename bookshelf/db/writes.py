"""How the models write: changes, savepoints, read committed transactions, retries."""

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager

from sqlalchemy import CursorResult, Executable
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.config import DatabaseBackend
from bookshelf.db.engine import READ_COMMITTED_TPB, TPB_OPTION, backend_of
from bookshelf.db.errors import is_concurrent_delete

__all__ = [
    "execute_change",
    "read_committed",
    "retry_after_concurrent_delete",
    "try_change",
]

logger = logging.getLogger(__name__)


@contextmanager
def read_committed(session: Session) -> Generator[Session]:
    """
    Run the block in a new read committed transaction of `session`, rolled
    back if the block raises; the block commits its work. The session's
    current transaction holds only reads (models commit what they write), and
    is ended first.

    A snapshot transaction could not use a row committed by someone else
    after it started: neither its lookups nor its foreign key checks would
    see that row, though its inserts clash with it.
    """
    session.rollback()
    match backend_of(session):
        case DatabaseBackend.FIREBIRD:
            options = {TPB_OPTION: READ_COMMITTED_TPB}
        case DatabaseBackend.POSTGRESQL:
            options = {"isolation_level": "READ COMMITTED"}
    session.connection(execution_options=options)
    try:
        yield session
    except BaseException:
        session.rollback()
        raise


def execute_change(session: Session, statement: Executable) -> CursorResult:
    """
    Run an INSERT, UPDATE or DELETE and return its result, for its rowcount
    (one with RETURNING goes through session.scalars instead: the ORM gives
    its rows, not a cursor's result). The session's objects are not synchronised:
    models keep none, and the "fetch" strategy would add a RETURNING clause,
    which Firebird 3 refuses for a statement changing several rows.
    """
    result = session.execute(
        statement,
        # The driver counts rows only while the cursor is open: kept for
        # an INSERT too, as SQLAlchemy does for UPDATE and DELETE.
        execution_options={"synchronize_session": False, "preserve_rowcount": True},
    )
    if not isinstance(result, CursorResult):
        raise TypeError(f"Not a change: {statement}")
    return result


def try_change(session: Session, statement: Executable) -> CursorResult:
    """
    execute_change in a savepoint, for a change whose error the caller
    handles: when it fails, only the change is undone, and the transaction
    goes on. PostgreSQL would otherwise refuse every later statement of the
    transaction; Firebird keeps it going either way.
    """
    with session.begin_nested():
        return execute_change(session, statement)


def retry_after_concurrent_delete[T](run: Callable[[], T]) -> T:
    """
    Return run(), running it once more if it changed a row that another
    transaction was deleting meanwhile: that deletion is committed now, so run
    again it sees the row gone. `run` uses its own read committed
    transaction, rolled back when it raises.
    """
    try:
        return run()
    except DBAPIError as e:
        if not is_concurrent_delete(e):
            raise
        logger.info("Running again after a concurrent delete: %s", e.orig)
    return run()
