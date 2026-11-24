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

from bookshelf.db.baseline import create_settings
from bookshelf.db.catalog import table_exists

__all__ = ["FUNCTIONS", "SchemaTooNewError", "init_db", "schema_behind"]

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
FUNCTIONS: dict[str, str] = {
    # The book lists' search: the title, an ISBN text or any author's
    # "name surname" contains the text, ignoring case (CONTAINING always does).
    # ISBNs are searched for two texts: see isbn_searches in models/books/isbn.py.
    # CONTAINING '' is true, so an empty search matches every book. NULL
    # (no ISBN, no author) counts as no match. With AUTHOR_FILTER, only that
    # author's books match.
    "BOOK_MATCHES": """
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
}


class SchemaTooNewError(RuntimeError):
    """The database has revisions this release does not know: it is newer."""

    def __init__(self, revision: str) -> None:
        super().__init__(
            f"The database schema is at revision {revision}, unknown to this "
            "release: run init-db from a release at least as new"
        )


def init_db(engine: Engine) -> None:
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
    """
    with engine.connect() as conn:
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
            _create_functions(conn)
        finally:
            lock.rollback()


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
    conn.execute(
        text(
            "MERGE INTO SETTINGS s USING RDB$DATABASE ON s.NAME = 'SCHEMA_UPGRADE_LOCK' "
            'WHEN NOT MATCHED THEN INSERT (NAME, "VALUE") '
            "VALUES ('SCHEMA_UPGRADE_LOCK', 'init-db')"
        )
    )
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
    if not table_exists(conn, "USERS") or table_exists(conn, "ALEMBIC_VERSION"):
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
    for ddl in FUNCTIONS.values():
        # As the driver's own SQL: in text(), PSQL's :VARIABLE references
        # would be taken for bound parameters.
        conn.exec_driver_sql(ddl)
    conn.commit()
