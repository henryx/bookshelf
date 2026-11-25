"""
The migrations' revisions (bookshelf/migrations/versions/), applied with
Alembic one at a time: a failed one is rolled back, one left halfway is rolled
back and applied again, and a Firebird database from before 1.5.0 is first
stamped from its SCHEMA_VERSION setting.
"""

import logging
from pathlib import Path

from alembic.ddl.impl import DefaultImpl
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import Script, ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from sqlalchemy import Connection, delete, insert, select, update

from bookshelf.config import DatabaseBackend
from bookshelf.db import backend_of
from bookshelf.db.catalog import table_exists
from bookshelf.db.orm import SettingRow

__all__ = ["MIGRATIONS", "SchemaTooNewError", "schema_behind", "upgrade"]

logger = logging.getLogger(__name__)

# The tests' `migrations` fixture replaces it: read it at each call.
MIGRATIONS = Path(__file__).parent.parent.parent / "migrations"

# The revision matching each SCHEMA_VERSION of the upgrade steps of releases
# before 1.5.0 (version n is after step n): version 0 is the 1.0.0 schema.
LEGACY_REVISIONS = ("0001", "0002")

# The setting naming the revision being applied or rolled back, while it is.
UPGRADE_STEP = "SCHEMA_UPGRADE_STEP"


class FirebirdImpl(DefaultImpl):
    """
    Alembic's support for the firebird-driver dialect, which has none of its
    own; registered by being defined. Alembic's defaults, with DDL outside
    transactions: each change commits on its own (see SchemaChange).
    """

    __dialect__ = "firebird.firebird"
    transactional_ddl = False


class SchemaTooNewError(RuntimeError):
    """The database has revisions this release does not know: it is newer."""

    def __init__(self, revision: str) -> None:
        super().__init__(
            f"The database schema is at revision {revision}, unknown to this "
            "release: run init-db from a release at least as new"
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


def upgrade(conn: Connection) -> None:
    """
    Apply the pending revisions in order, after stamping a database from
    before Alembic and rolling back a revision left halfway.
    SchemaTooNewError when the database has revisions this release does not
    know.
    """
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
    step = _legacy_setting(conn, UPGRADE_STEP)
    if step is not None:
        if step >= len(LEGACY_REVISIONS):
            raise SchemaTooNewError(f"(upgrade step {step})")
        conn.execute(
            update(SettingRow)
            .where(SettingRow.name == UPGRADE_STEP)
            .values(value=LEGACY_REVISIONS[step])
        )
        conn.commit()
    _stamp(conn, context, script, LEGACY_REVISIONS[version])
    logger.info(
        "Schema version %d stamped as revision %s", version, LEGACY_REVISIONS[version]
    )


def _legacy_setting(conn: Connection, name: str) -> int | None:
    value = _setting(conn, name)
    return None if value is None else int(value)


def _setting(conn: Connection, name: str) -> str | None:
    return conn.execute(
        select(SettingRow.value).where(SettingRow.name == name)
    ).scalar()


def _finish_interrupted(
    conn: Connection, context: MigrationContext, script: ScriptDirectory
) -> None:
    """Roll back the revision a crash or a failed rollback left halfway, if any."""
    marked = _setting(conn, UPGRADE_STEP)
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
    _clear_marker(conn)
    conn.commit()
    logger.info("Revision %s applied", revision.revision)


def _roll_back(conn: Connection, context: MigrationContext, revision: Script) -> None:
    """Undo a revision; if this fails, it stays marked and the next run tries again."""
    with Operations.context(context):
        revision.module.downgrade()
    _clear_marker(conn)
    conn.commit()
    logger.info("Revision %s rolled back", revision.revision)


def _set_marker(conn: Connection, revision: str) -> None:
    conn.execute(insert(SettingRow).values(name=UPGRADE_STEP, value=revision))
    conn.commit()


def _clear_marker(conn: Connection) -> None:
    conn.execute(delete(SettingRow).where(SettingRow.name == UPGRADE_STEP))


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
