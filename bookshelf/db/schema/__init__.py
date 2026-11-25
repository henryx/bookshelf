"""
Creating and upgrading the schema: init_db applies the migrations'
revisions (bookshelf/migrations/versions/) with Alembic (`revisions.py`),
then creates or replaces the PSQL functions (`functions.py`); `server.py`
checks the server and records PostgreSQL's search language. README
"Migrations" has the design.
"""

from sqlalchemy import Connection, Engine, exists, insert, select, update
from sqlalchemy.exc import DBAPIError

from bookshelf.config import DEFAULT_SEARCH_LANGUAGE, DatabaseBackend
from bookshelf.db import backend_of, is_unique_violation
from bookshelf.db.baseline import create_settings
from bookshelf.db.orm import SettingRow
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


# The setting each run updates, uncommitted, to keep the others waiting.
UPGRADE_LOCK = "SCHEMA_UPGRADE_LOCK"


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
            update(SettingRow)
            .where(SettingRow.name == UPGRADE_LOCK)
            .values(value=SettingRow.value)
        )
        try:
            upgrade(conn)
            if backend_of(conn) is DatabaseBackend.POSTGRESQL:
                set_search_language(conn, search_language)
            create_functions(conn)
        finally:
            lock.rollback()


def _add_lock(conn: Connection) -> None:
    """
    Add the lock's row, unless there is one: another run may be adding it
    meanwhile, and then this insert waits for it and, once it is committed,
    fails as a duplicate, which is fine.
    """
    if conn.execute(select(exists().where(SettingRow.name == UPGRADE_LOCK))).scalar():
        conn.rollback()
        return
    try:
        conn.execute(insert(SettingRow).values(name=UPGRADE_LOCK, value="init-db"))
    except DBAPIError as e:
        conn.rollback()
        if not is_unique_violation(e):
            raise
        return
    conn.commit()
