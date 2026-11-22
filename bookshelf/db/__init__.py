import logging
from collections.abc import Callable
from functools import partial
from queue import Empty, Full, Queue

from firebird.driver import Connection, DatabaseError, connect
from flask import Flask, current_app, g

__all__ = [
    "ConnectionPool",
    "close_pool",
    "get_db",
    "init_app",
    "retry_after_concurrent_delete",
]

_POOL_KEY = "bookshelf.db_pool"
# Firebird GDS code for "cannot update erased record": a row the statement
# changes was deleted by a transaction it waited for, which then committed.
_ERASED_RECORD = 335544382

logger = logging.getLogger(__name__)


class ConnectionPool:
    """
    Thread-safe pool keeping up to `size` idle connections for reuse. More
    connections may be open at once; the extra ones are closed when returned.
    Connections are opened on first use, so each gunicorn worker opens its
    own (as long as nothing queries the database before the fork).
    """

    def __init__(self, connect: Callable[[], Connection], size: int) -> None:
        self._connect = connect
        self._idle: Queue[Connection] = Queue(maxsize=size)

    def acquire(self) -> Connection:
        """Return a live idle connection, or a new one if none is left."""
        while True:
            try:
                conn = self._idle.get_nowait()
            except Empty:
                return self._connect()
            try:
                conn.ping()
                return conn
            except DatabaseError:
                logger.warning("Discarding dead pooled connection", exc_info=True)
                _discard(conn)

    def release(self, conn: Connection) -> None:
        """Roll back unfinished work and keep the connection for reuse."""
        if conn.is_closed():
            return
        try:
            if conn.is_active():
                conn.rollback()
        except DatabaseError:
            logger.warning("Discarding connection that failed rollback", exc_info=True)
            _discard(conn)
            return
        try:
            self._idle.put_nowait(conn)
        except Full:
            _discard(conn)

    def close(self) -> None:
        """Close every idle connection."""
        while True:
            try:
                conn = self._idle.get_nowait()
            except Empty:
                return
            _discard(conn)


def _discard(conn: Connection) -> None:
    try:
        conn.close()
    except DatabaseError:
        logger.warning("Error closing database connection", exc_info=True)


def _open(config: dict[str, object]) -> Connection:
    return connect(
        str(config["DATABASE_DSN"]),
        user=str(config["DATABASE_USER"]),
        password=str(config["DATABASE_PASSWORD"]),
        charset="UTF8",
    )


def _pool(app: Flask) -> ConnectionPool:
    return app.extensions[_POOL_KEY]


def get_db() -> Connection:
    """
    Return the Firebird connection for the current app context, taking one
    from the pool if needed. It goes back to the pool when the context ends.
    """
    if "db" not in g:
        g.db = _pool(current_app).acquire()
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        _pool(current_app).release(conn)


def close_pool(app: Flask) -> None:
    """Close the app's idle connections."""
    _pool(app).close()


def init_app(app: Flask) -> None:
    app.extensions[_POOL_KEY] = ConnectionPool(
        partial(_open, app.config), app.config["DATABASE_POOL_SIZE"]
    )
    app.teardown_appcontext(close_db)


def retry_after_concurrent_delete[T](run: Callable[[], T]) -> T:
    """
    Return run(), running it once more if it changed a row that another
    transaction was deleting meanwhile: that deletion is committed now, so run
    again it sees the row gone. `run` uses its own read committed
    transaction, rolled back when it raises.
    """
    try:
        return run()
    except DatabaseError as e:
        if _ERASED_RECORD not in e.gds_codes:
            raise
        logger.info("Running again after a concurrent delete: %s", e)
    return run()
