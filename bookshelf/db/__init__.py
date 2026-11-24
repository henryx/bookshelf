import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import cast

import firebird.driver
from firebird.driver import Connection as FirebirdConnection
from firebird.driver import DatabaseError, Isolation, connect, tpb
from flask import Flask, current_app, g
from sqlalchemy import (
    URL,
    Connection,
    CursorResult,
    Engine,
    Executable,
    create_engine,
    event,
)
from sqlalchemy.dialects import registry
from sqlalchemy.engine.interfaces import DBAPIConnection, DBAPIModule
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry
from sqlalchemy_firebird.firebird import FBDialect_firebird

from bookshelf.config import DatabaseBackend

__all__ = [
    "backend_of",
    "close_pool",
    "create_db_engine",
    "execute_change",
    "get_engine",
    "get_session",
    "init_app",
    "is_concurrent_delete",
    "is_foreign_key_violation",
    "is_unique_violation",
    "is_update_conflict",
    "read_committed",
    "retry_after_concurrent_delete",
    "try_change",
]

_ENGINE_KEY = "bookshelf.db_engine"
_SESSIONS_KEY = "bookshelf.db_sessions"

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

# The execution option naming a transaction's parameters (see read_committed);
# without it, a transaction uses the driver's default (snapshot).
_TPB_OPTION = "bookshelf_tpb"
_DEFAULT_TPB = "bookshelf_default_tpb"
READ_COMMITTED_TPB = tpb(Isolation.READ_COMMITTED_RECORD_VERSION)

logger = logging.getLogger(__name__)


class FirebirdDialect(FBDialect_firebird):
    """
    sqlalchemy-firebird's firebird-driver dialect, with what it gets wrong
    about firebird-driver 2.x fixed. Transaction isolation is set by the
    engine's events instead (see create_db_engine): the dialect's own calls
    a method the driver does not have.
    """

    # Declared by each dialect class: SQLAlchemy caches compiled statements.
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls) -> DBAPIModule:
        # Defined here, not inherited: SQLAlchemy warns about the parent's
        # deprecated dbapi() otherwise.
        return cast(DBAPIModule, firebird.driver)

    def do_terminate(self, dbapi_connection: DBAPIConnection) -> None:
        # The driver's connections have no terminate(): close them.
        dbapi_connection.close()

    def do_ping(self, dbapi_connection: DBAPIConnection) -> bool:
        """
        Whether a pooled connection is alive. The dialect's own check runs a
        query, which on a closed connection raises a TypeError, not taken
        for a lost connection; the driver's ping starts no transaction.
        """
        conn = cast(FirebirdConnection, dbapi_connection)
        if conn.is_closed():
            return False
        try:
            conn.ping()
        except DatabaseError:
            logger.warning("Discarding dead pooled connection", exc_info=True)
            return False
        return True


registry.register("firebird.bookshelf", __name__, "FirebirdDialect")


def create_db_engine(
    backend: DatabaseBackend, dsn: str, user: str, password: str, *, pool_size: int
) -> Engine:
    """
    An engine on the database `dsn` (see Config.db_dsn). It keeps up to
    `pool_size` idle connections; more may be open at once, and the extra
    ones are closed when returned. Connections are opened on first use, so
    each gunicorn worker opens its own (as long as nothing queries the
    database before the fork), and checked before reuse: a dead one is
    replaced. Transactions are snapshots unless read_committed asks
    otherwise: Firebird's default, PostgreSQL's repeatable read.
    """
    match backend:
        case DatabaseBackend.FIREBIRD:
            return _firebird_engine(dsn, user, password, pool_size)
        case DatabaseBackend.POSTGRESQL:
            return _postgresql_engine(dsn, user, password, pool_size)


def _postgresql_engine(dsn: str, user: str, password: str, pool_size: int) -> Engine:
    address, _, database = dsn.partition("/")
    host, _, port = address.partition(":")
    url = URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=int(port) if port else None,
        database=database,
    )
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=-1,
        pool_pre_ping=True,
        isolation_level="REPEATABLE READ",
    )


def _firebird_engine(dsn: str, user: str, password: str, pool_size: int) -> Engine:
    def open_connection() -> FirebirdConnection:
        return connect(dsn, user=user, password=password, charset="UTF8")

    engine = create_engine(
        "firebird+bookshelf://",
        creator=open_connection,
        pool_size=pool_size,
        max_overflow=-1,
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", _remember_default_tpb)
    event.listen(engine, "begin", _set_transaction_parameters)
    return engine


def _remember_default_tpb(
    conn: FirebirdConnection, record: ConnectionPoolEntry
) -> None:
    record.info[_DEFAULT_TPB] = conn.main_transaction.default_tpb


def _set_transaction_parameters(conn: Connection) -> None:
    """
    Give the transaction starting on `conn` the parameters its execution
    options ask for, else the driver's default. firebird-driver starts a
    transaction with the first statement, so this applies to it: none runs
    before, since every transaction ends with SQLAlchemy's and the pool's
    liveness check (do_ping) starts none.
    """
    raw = cast(FirebirdConnection, conn.connection.dbapi_connection)
    default = conn.connection.info[_DEFAULT_TPB]
    raw.main_transaction.default_tpb = conn.get_execution_options().get(
        _TPB_OPTION, default
    )


def backend_of(bind: Session | Connection | Engine) -> DatabaseBackend:
    """The database server a session, connection or engine talks to."""
    engine = bind.get_bind() if isinstance(bind, Session) else bind
    if engine.dialect.name == "postgresql":
        return DatabaseBackend.POSTGRESQL
    return DatabaseBackend.FIREBIRD


def init_app(app: Flask) -> None:
    cfg = app.config
    engine = create_db_engine(
        DatabaseBackend(cfg["DATABASE_BACKEND"]),
        str(cfg["DATABASE_DSN"]),
        str(cfg["DATABASE_USER"]),
        str(cfg["DATABASE_PASSWORD"]),
        pool_size=int(cfg["DATABASE_POOL_SIZE"]),
    )
    app.extensions[_ENGINE_KEY] = engine
    app.extensions[_SESSIONS_KEY] = sessionmaker(engine)
    app.teardown_appcontext(close_session)


def get_engine() -> Engine:
    """The current app's engine."""
    return current_app.extensions[_ENGINE_KEY]


def get_session() -> Session:
    """
    Return the session for the current app context, opening one if needed.
    It is closed when the context ends, rolling back what it left
    uncommitted, and its connection goes back to the pool.
    """
    if "db_session" not in g:
        g.db_session = current_app.extensions[_SESSIONS_KEY]()
    return g.db_session


def close_session(_exc: BaseException | None = None) -> None:
    session = g.pop("db_session", None)
    if session is not None:
        session.close()


def close_pool(app: Flask) -> None:
    """Close the app's idle connections."""
    app.extensions[_ENGINE_KEY].dispose()


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
            options = {_TPB_OPTION: READ_COMMITTED_TPB}
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
