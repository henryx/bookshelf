"""
Creating and upgrading the schema: init_db applies the migrations'
revisions (bookshelf/migrations/versions/) with Alembic (`revisions.py`),
then creates or replaces the PSQL functions (`functions.py`); `server.py`
checks the server and records PostgreSQL's search language. README
"Migrations" has the design.
"""

from sqlalchemy import Connection, Engine, text

from bookshelf.config import DEFAULT_SEARCH_LANGUAGE, DatabaseBackend
from bookshelf.db import backend_of
from bookshelf.db.baseline import create_settings
from bookshelf.db.changes import Sql, statement
from bookshelf.db.schema.functions import FUNCTIONS, create_functions
from bookshelf.db.schema.revisions import SchemaTooNewError, schema_behind, upgrade
from bookshelf.db.schema.server import (
    UnknownSearchLanguageError,
    UnsupportedServerError,
    check_server,
    set_search_language,
)

__all__ = [
    "FUNCTIONS",
    "SchemaTooNewError",
    "UnknownSearchLanguageError",
    "UnsupportedServerError",
    "init_db",
    "schema_behind",
]


def init_db(engine: Engine, *, search_language: str = DEFAULT_SEARCH_LANGUAGE) -> None:
    """
    Apply the pending revisions in order, after rolling back one left
    halfway, then create or replace the functions. Safe to run more than
    once; a second run waits for one running meanwhile. A database from
    before Alembic is first stamped with the revision matching its
    SCHEMA_VERSION.

    A revision that fails is rolled back (its downgrade) and its error
    raised; one left halfway (by a crash, or a failed rollback) is rolled
    back, then applied again. SchemaTooNewError when the database has
    revisions this release does not know.

    On PostgreSQL, also records `search_language`, the text search
    configuration of the searches (UnknownSearchLanguageError when the server
    has none by that name).
    """
    with engine.connect() as conn:
        check_server(conn)
        create_settings(conn)
        _add_lock(conn)
    with engine.connect() as lock, engine.connect() as conn:
        # Written nowhere else: holding it keeps other runs waiting until
        # this one ends. Always rolled back.
        lock.execute(
            text(
                'UPDATE SETTINGS SET "VALUE" = "VALUE" '
                "WHERE NAME = 'SCHEMA_UPGRADE_LOCK'"
            )
        )
        try:
            upgrade(conn)
            if backend_of(conn) is DatabaseBackend.POSTGRESQL:
                set_search_language(conn, search_language)
            create_functions(conn)
        finally:
            lock.rollback()


def _add_lock(conn: Connection) -> None:
    sql: Sql = {
        DatabaseBackend.FIREBIRD: (
            "MERGE INTO SETTINGS s USING RDB$DATABASE "
            "ON s.NAME = 'SCHEMA_UPGRADE_LOCK' "
            'WHEN NOT MATCHED THEN INSERT (NAME, "VALUE") '
            "VALUES ('SCHEMA_UPGRADE_LOCK', 'init-db')"
        ),
        DatabaseBackend.POSTGRESQL: (
            'INSERT INTO SETTINGS (NAME, "VALUE") '
            "VALUES ('SCHEMA_UPGRADE_LOCK', 'init-db') ON CONFLICT (NAME) DO NOTHING"
        ),
    }
    conn.execute(text(statement(conn, sql)))
    conn.commit()
