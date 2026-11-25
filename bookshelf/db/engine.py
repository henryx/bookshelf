"""
The engines, per backend: Firebird's dialect (sqlalchemy-firebird with
firebird-driver fixes) and its transaction parameters, and PostgreSQL's
psycopg engine.
"""

import logging
from typing import cast

import firebird.driver
from firebird.driver import Connection as FirebirdConnection
from firebird.driver import DatabaseError, Isolation, connect, tpb
from sqlalchemy import URL, Connection, Engine, create_engine, event
from sqlalchemy.dialects import registry
from sqlalchemy.engine.interfaces import DBAPIConnection, DBAPIModule
from sqlalchemy.orm import Session
from sqlalchemy.pool import ConnectionPoolEntry
from sqlalchemy_firebird.firebird import FBDialect_firebird

from bookshelf.config import DatabaseBackend

__all__ = [
    "READ_COMMITTED_TPB",
    "TPB_OPTION",
    "FirebirdDialect",
    "backend_of",
    "create_db_engine",
]

# The execution option naming a transaction's parameters (see read_committed);
# without it, a transaction uses the driver's default (snapshot).
TPB_OPTION = "bookshelf_tpb"
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
        TPB_OPTION, default
    )


def backend_of(bind: Session | Connection | Engine) -> DatabaseBackend:
    """The database server a session, connection or engine talks to."""
    engine = bind.get_bind() if isinstance(bind, Session) else bind
    if engine.dialect.name == "postgresql":
        return DatabaseBackend.POSTGRESQL
    return DatabaseBackend.FIREBIRD
