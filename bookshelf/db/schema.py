"""
Creating and upgrading the schema: init_db applies the migrations'
revisions (bookshelf/migrations/versions/) with Alembic, then creates or
replaces the PSQL functions. README "Migrations" has the design.
"""

import logging
from pathlib import Path

from alembic.ddl.impl import DefaultImpl
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import Script, ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from sqlalchemy import Connection, Engine, text

from bookshelf.config import DEFAULT_SEARCH_LANGUAGE, DatabaseBackend
from bookshelf.db import backend_of
from bookshelf.db.baseline import create_settings
from bookshelf.db.catalog import table_exists
from bookshelf.db.changes import Sql, statement

__all__ = [
    "FUNCTIONS",
    "SchemaTooNewError",
    "UnknownSearchLanguageError",
    "UnsupportedServerError",
    "init_db",
    "schema_behind",
]

logger = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).parent.parent / "migrations"

# The revision matching each SCHEMA_VERSION of the upgrade steps of releases
# before 1.5.0 (version n is after step n): version 0 is the 1.0.0 schema.
LEGACY_REVISIONS = ("0001", "0002")


class FirebirdImpl(DefaultImpl):
    """
    Alembic's support for the firebird-driver dialect, which has none of its
    own; registered by being defined. Alembic's defaults, with DDL outside
    transactions: each change commits on its own (see SchemaChange).
    """

    __dialect__ = "firebird.firebird"
    transactional_ddl = False


# PSQL functions, created after the revisions are applied. Each is created or
# replaced on every init_db, so its body can change without a revision.
FUNCTIONS: dict[str, Sql] = {
    # The book lists' search: the title, an ISBN text or any author's
    # "name surname" contains the text, ignoring case (CONTAINING always does).
    # ISBNs are searched for two texts: see isbn_searches in models/books/isbn.py.
    # CONTAINING '' is true, so an empty search matches every book. NULL
    # (no ISBN, no author) counts as no match. With AUTHOR_FILTER, only that
    # author's books match.
    # PostgreSQL searches words instead (SEARCH_MATCHES): each word of the text
    # starts a word of the title or of an author's name and surname, all of
    # them read as one text, after stemming ("running" finds "run"); ISBNs
    # still contain their texts. Its parameters are named apart from the
    # columns.
    "BOOK_MATCHES": {
        DatabaseBackend.FIREBIRD: """
        CREATE OR ALTER FUNCTION BOOK_MATCHES (
            BOOK_ID TYPE OF COLUMN BOOKS.ID,
            BOOK_TITLE TYPE OF COLUMN BOOKS.TITLE,
            BOOK_ISBN TYPE OF COLUMN BOOKS.ISBN,
            SEARCH_TEXT VARCHAR(255),
            ISBN_TEXT VARCHAR(255),
            ISBN_13_TEXT VARCHAR(255),
            AUTHOR_FILTER TYPE OF COLUMN AUTHORS.ID
        ) RETURNS BOOLEAN
        AS
        BEGIN
            RETURN (
                AUTHOR_FILTER IS NULL
                OR EXISTS (
                    SELECT 1 FROM BOOK_AUTHORS fa
                    WHERE fa.BOOK_ID = :BOOK_ID AND fa.AUTHOR_ID = :AUTHOR_FILTER
                )
            ) AND COALESCE(
                BOOK_TITLE CONTAINING SEARCH_TEXT
                OR BOOK_ISBN CONTAINING ISBN_TEXT
                OR BOOK_ISBN CONTAINING ISBN_13_TEXT
                OR EXISTS (
                    SELECT 1 FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
                    WHERE ba.BOOK_ID = :BOOK_ID
                      AND (a.NAME || ' ' || a.SURNAME) CONTAINING :SEARCH_TEXT
                ),
                FALSE
            );
        END
    """,
        DatabaseBackend.POSTGRESQL: r"""
            CREATE OR REPLACE FUNCTION BOOK_MATCHES (
                P_BOOK_ID UUID,
                P_BOOK_TITLE VARCHAR,
                P_BOOK_ISBN CHAR(13),
                P_SEARCH_TEXT VARCHAR,
                P_ISBN_TEXT VARCHAR,
                P_ISBN_13_TEXT VARCHAR,
                P_AUTHOR_FILTER UUID
            ) RETURNS BOOLEAN
            LANGUAGE SQL STABLE
            AS $$
                SELECT (
                    P_AUTHOR_FILTER IS NULL
                    OR EXISTS (
                        SELECT 1 FROM BOOK_AUTHORS fa
                        WHERE fa.BOOK_ID = P_BOOK_ID AND fa.AUTHOR_ID = P_AUTHOR_FILTER
                    )
                ) AND (
                    SEARCH_MATCHES(
                        P_BOOK_TITLE || ' ' || COALESCE((
                            SELECT STRING_AGG(a.NAME || ' ' || a.SURNAME, ' ')
                            FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
                            WHERE ba.BOOK_ID = P_BOOK_ID
                        ), ''),
                        P_SEARCH_TEXT
                    )
                    OR COALESCE(
                        P_BOOK_ISBN LIKE CONTAINS_PATTERN(P_ISBN_TEXT) ESCAPE '\'
                        OR P_BOOK_ISBN LIKE CONTAINS_PATTERN(P_ISBN_13_TEXT) ESCAPE '\',
                        FALSE
                    )
                )
            $$
        """,
    },
}

# PostgreSQL's helpers, created before the functions using them, in order.
POSTGRESQL_HELPERS: dict[str, str] = {
    # The text search configuration init_db recorded (APP_SEARCH_LANGUAGE).
    "SEARCH_CONFIG": r"""
        CREATE OR REPLACE FUNCTION SEARCH_CONFIG ()
        RETURNS REGCONFIG
        LANGUAGE SQL STABLE
        AS $$
            SELECT COALESCE(
                (SELECT "VALUE" FROM SETTINGS WHERE NAME = 'SEARCH_LANGUAGE'),
                'simple'
            )::REGCONFIG
        $$
    """,
    # The query for a search text: each of its words, stemmed, as the start
    # of a word (prefix matching, so "leck" finds "Leckie"), all of them
    # required. NULL when the text has no words but stop words ("the").
    "SEARCH_QUERY": r"""
        CREATE OR REPLACE FUNCTION SEARCH_QUERY (P_TEXT VARCHAR)
        RETURNS TSQUERY
        LANGUAGE SQL STABLE
        AS $$
            SELECT TO_TSQUERY(
                'simple', STRING_AGG(QUOTE_LITERAL(word) || ':*', ' & ')
            )
            FROM UNNEST(TSVECTOR_TO_ARRAY(TO_TSVECTOR(SEARCH_CONFIG(), P_TEXT))) AS word
        $$
    """,
    # Whether a document matches a search text (SEARCH_QUERY); a text with
    # no words (empty, or only stop words) matches every document.
    "SEARCH_MATCHES": r"""
        CREATE OR REPLACE FUNCTION SEARCH_MATCHES (P_DOCUMENT VARCHAR, P_TEXT VARCHAR)
        RETURNS BOOLEAN
        LANGUAGE SQL STABLE
        AS $$
            SELECT COALESCE(
                TO_TSVECTOR(SEARCH_CONFIG(), P_DOCUMENT) @@ SEARCH_QUERY(P_TEXT),
                SEARCH_QUERY(P_TEXT) IS NULL
            )
        $$
    """,
    # The LIKE pattern for "contains this text, as typed": for ISBNs.
    "CONTAINS_PATTERN": r"""
        CREATE OR REPLACE FUNCTION CONTAINS_PATTERN (P_TEXT VARCHAR)
        RETURNS VARCHAR
        LANGUAGE SQL IMMUTABLE
        AS $$
            SELECT '%' || REPLACE(REPLACE(REPLACE(P_TEXT, '\', '\\'), '%', '\%'), '_', '\_') || '%'
        $$
    """,
}


class SchemaTooNewError(RuntimeError):
    """The database has revisions this release does not know: it is newer."""

    def __init__(self, revision: str) -> None:
        super().__init__(
            f"The database schema is at revision {revision}, unknown to this "
            "release: run init-db from a release at least as new"
        )


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
        _check_server(conn)
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
            _upgrade(conn)
            if backend_of(conn) is DatabaseBackend.POSTGRESQL:
                _set_search_language(conn, search_language)
            _create_functions(conn)
        finally:
            lock.rollback()


class UnsupportedServerError(RuntimeError):
    """A database server too old for this release."""


class UnknownSearchLanguageError(ValueError):
    """APP_SEARCH_LANGUAGE names no text search configuration of the server."""

    def __init__(self, language: str) -> None:
        super().__init__(
            f"PostgreSQL has no text search configuration {language!r} "
            "(APP_SEARCH_LANGUAGE): see SELECT cfgname FROM pg_ts_config"
        )
        self.language = language


def _set_search_language(conn: Connection, language: str) -> None:
    """Record the searches' text search configuration (see SEARCH_CONFIG)."""
    known = conn.execute(
        text("SELECT 1 FROM pg_catalog.pg_ts_config WHERE cfgname = :name"),
        {"name": language},
    ).first()
    if known is None:
        raise UnknownSearchLanguageError(language)
    conn.execute(
        text(
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SEARCH_LANGUAGE', :language) "
            'ON CONFLICT (NAME) DO UPDATE SET "VALUE" = EXCLUDED."VALUE"'
        ),
        {"language": language},
    )
    conn.commit()


# PostgreSQL 18: LIKE on a nondeterministic collation (UNICODE_CI).
MIN_POSTGRESQL_VERSION = 180000


def _check_server(conn: Connection) -> None:
    if backend_of(conn) is not DatabaseBackend.POSTGRESQL:
        return
    version = int(conn.execute(text("SHOW server_version_num")).scalar_one())
    conn.rollback()
    if version < MIN_POSTGRESQL_VERSION:
        raise UnsupportedServerError(
            f"Bookshelf needs PostgreSQL 18 or newer; this server is {version}"
        )


def schema_behind(conn: Connection) -> bool:
    """
    True when the database has a schema, but older than this release's: it
    needs init-db. No schema at all is not behind: setup creates it; nor is
    one at a revision this release does not know (newer).
    """
    if not table_exists(conn, "USERS"):
        return False
    script = _scripts()
    heads = MigrationContext.configure(conn).get_current_heads()
    if not heads:
        return True
    if any(_known(script, head) is None for head in heads):
        return False
    return heads != (script.get_current_head(),)


def _scripts() -> ScriptDirectory:
    return ScriptDirectory(str(MIGRATIONS))


def _known(script: ScriptDirectory, revision: str) -> Script | None:
    try:
        return script.get_revision(revision)
    except CommandError, RevisionError:
        return None


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


def _upgrade(conn: Connection) -> None:
    script = _scripts()
    context = MigrationContext.configure(conn)
    _stamp_legacy(conn, context, script)
    _finish_interrupted(conn, context, script)
    heads = context.get_current_heads()
    for head in heads:
        if _known(script, head) is None:
            raise SchemaTooNewError(head)
    current = heads[0] if heads else "base"
    pending = reversed(list(script.iterate_revisions("heads", current)))
    for revision in pending:
        _apply(conn, context, script, revision)


def _stamp_legacy(
    conn: Connection, context: MigrationContext, script: ScriptDirectory
) -> None:
    """
    Give a database from before Alembic (tables, but no version table) the
    revision matching its SCHEMA_VERSION; a 1.0.x one has none: 0. A step
    left halfway by those releases is marked as its revision, to be rolled
    back like one.
    """
    if (
        backend_of(conn) is not DatabaseBackend.FIREBIRD
        or not table_exists(conn, "USERS")
        or table_exists(conn, "ALEMBIC_VERSION")
    ):
        return
    version = _legacy_setting(conn, "SCHEMA_VERSION") or 0
    if version >= len(LEGACY_REVISIONS):
        raise SchemaTooNewError(f"(upgrade step {version})")
    step = _legacy_setting(conn, "SCHEMA_UPGRADE_STEP")
    if step is not None:
        if step >= len(LEGACY_REVISIONS):
            raise SchemaTooNewError(f"(upgrade step {step})")
        conn.execute(
            text(
                'UPDATE SETTINGS SET "VALUE" = :rev '
                "WHERE NAME = 'SCHEMA_UPGRADE_STEP'"
            ),
            {"rev": LEGACY_REVISIONS[step]},
        )
        conn.commit()
    _stamp(conn, context, script, LEGACY_REVISIONS[version])
    logger.info(
        "Schema version %d stamped as revision %s", version, LEGACY_REVISIONS[version]
    )


def _legacy_setting(conn: Connection, name: str) -> int | None:
    value = conn.execute(
        text('SELECT "VALUE" FROM SETTINGS WHERE NAME = :name'), {"name": name}
    ).scalar()
    return None if value is None else int(value)


def _finish_interrupted(
    conn: Connection, context: MigrationContext, script: ScriptDirectory
) -> None:
    """Roll back the revision a crash or a failed rollback left halfway, if any."""
    marked = conn.execute(
        text("SELECT \"VALUE\" FROM SETTINGS WHERE NAME = 'SCHEMA_UPGRADE_STEP'")
    ).scalar()
    conn.commit()
    if marked is None:
        return
    revision = _known(script, marked)
    if revision is None:
        raise SchemaTooNewError(marked)
    logger.warning("Revision %s was left halfway: rolling it back", marked)
    _roll_back(conn, context, revision)


def _apply(
    conn: Connection,
    context: MigrationContext,
    script: ScriptDirectory,
    revision: Script,
) -> None:
    _set_marker(conn, revision.revision)
    logger.info("Applying revision %s", revision.revision)
    try:
        with Operations.context(context):
            revision.module.upgrade()
    except Exception:
        logger.exception("Revision %s failed: rolling it back", revision.revision)
        conn.rollback()
        _roll_back(conn, context, revision)
        raise
    _stamp(conn, context, script, revision.revision, commit=False)
    conn.execute(text("DELETE FROM SETTINGS WHERE NAME = 'SCHEMA_UPGRADE_STEP'"))
    conn.commit()
    logger.info("Revision %s applied", revision.revision)


def _roll_back(conn: Connection, context: MigrationContext, revision: Script) -> None:
    """Undo a revision; if this fails, it stays marked and the next run tries again."""
    with Operations.context(context):
        revision.module.downgrade()
    conn.execute(text("DELETE FROM SETTINGS WHERE NAME = 'SCHEMA_UPGRADE_STEP'"))
    conn.commit()
    logger.info("Revision %s rolled back", revision.revision)


def _set_marker(conn: Connection, revision: str) -> None:
    conn.execute(
        text(
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', :rev)"
        ),
        {"rev": revision},
    )
    conn.commit()


def _stamp(
    conn: Connection,
    context: MigrationContext,
    script: ScriptDirectory,
    revision: str,
    *,
    commit: bool = True,
) -> None:
    """
    Record the database as at `revision`. The version table is created
    first, committed on its own: Firebird can use a table only once its
    creation is committed.
    """
    if not context.get_current_heads():
        # Stamping the base creates the version table and adds no row.
        context.stamp(script, "base")
        conn.commit()
    context.stamp(script, revision)
    if commit:
        conn.commit()


def _create_functions(conn: Connection) -> None:
    functions = list(FUNCTIONS.values())
    if backend_of(conn) is DatabaseBackend.POSTGRESQL:
        functions = [*POSTGRESQL_HELPERS.values(), *functions]
    for ddl in functions:
        # As the driver's own SQL: in text(), PSQL's :VARIABLE references
        # would be taken for bound parameters. psycopg reads % as one, so
        # PostgreSQL's LIKE wildcards are doubled.
        sql = statement(conn, ddl)
        if backend_of(conn) is DatabaseBackend.POSTGRESQL:
            sql = sql.replace("%", "%%")
        conn.exec_driver_sql(sql)
    conn.commit()
