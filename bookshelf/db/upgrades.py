"""
Schema upgrade steps: the changes to released schema objects, and applying
them. The version is the SCHEMA_VERSION setting (see init_db in schema.py,
which calls apply_upgrades); README "Upgrade steps" has the design.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from firebird.driver import Connection

from bookshelf.db.catalog import (
    column_exists,
    column_nullable,
    constraint_exists,
    table_exists,
)

__all__ = [
    "UPGRADES",
    "USERS_AUTH_METHOD",
    "SchemaChange",
    "SchemaTooNewError",
    "UpgradeStep",
    "apply_upgrades",
    "schema_version",
    "upgrades_pending",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpgradeStep:
    """
    A change to released schema objects. `upgrade` applies it; `rollback`
    undoes it, from wherever a failed `upgrade` stopped. Both check each
    change before making it, so either can run again after failing halfway.
    Both commit their own work; what `upgrade` leaves uncommitted is
    committed with its version.
    """

    upgrade: Callable[[Connection], None]
    rollback: Callable[[Connection], None]


@dataclass(frozen=True)
class SchemaChange:
    """
    One change of an upgrade step, rerunnable both ways: apply_sql runs
    unless `applied` says it is already made, undo_sql only if it is.
    Each commits on its own (Firebird makes DDL usable only once committed).
    """

    applied: Callable[[Connection], bool]
    apply_sql: str
    undo_sql: str

    def apply(self, conn: Connection) -> None:
        if not self.applied(conn):
            with conn.cursor() as cur:
                cur.execute(self.apply_sql)
            conn.commit()

    def undo(self, conn: Connection) -> None:
        if self.applied(conn):
            with conn.cursor() as cur:
                cur.execute(self.undo_sql)
            conn.commit()


# Step 1 (1.2.0): how each user logs in. AUTH_METHOD is LOCAL (a password,
# the default, so existing users keep theirs) or GOOGLE (no password:
# PASSWORD_HASH becomes nullable). GOOGLE_SUBJECT is the Google account a
# Google user is bound to on their first login (Google's "sub", at most 255
# characters), NULL until then; unique, ignoring NULLs. Applied in order,
# undone in reverse.
USERS_AUTH_METHOD: list[SchemaChange] = [
    SchemaChange(
        lambda conn: column_exists(conn, "USERS", "AUTH_METHOD"),
        "ALTER TABLE USERS ADD AUTH_METHOD VARCHAR(16) DEFAULT 'LOCAL' NOT NULL",
        "ALTER TABLE USERS DROP AUTH_METHOD",
    ),
    SchemaChange(
        lambda conn: column_exists(conn, "USERS", "GOOGLE_SUBJECT"),
        "ALTER TABLE USERS ADD GOOGLE_SUBJECT VARCHAR(255)",
        "ALTER TABLE USERS DROP GOOGLE_SUBJECT",
    ),
    # Undone only while no user lacks a password: before any Google user,
    # which is when a failed step 1 is rolled back.
    SchemaChange(
        lambda conn: column_nullable(conn, "USERS", "PASSWORD_HASH"),
        "ALTER TABLE USERS ALTER PASSWORD_HASH DROP NOT NULL",
        "ALTER TABLE USERS ALTER PASSWORD_HASH SET NOT NULL",
    ),
    SchemaChange(
        lambda conn: constraint_exists(conn, "CHK_USERS_AUTH_METHOD"),
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_AUTH_METHOD "
        "CHECK (AUTH_METHOD IN ('LOCAL', 'GOOGLE'))",
        "ALTER TABLE USERS DROP CONSTRAINT CHK_USERS_AUTH_METHOD",
    ),
    # A local user has a password and no Google account; a Google user no password.
    SchemaChange(
        lambda conn: constraint_exists(conn, "CHK_USERS_CREDENTIALS"),
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_CREDENTIALS CHECK ("
        "(AUTH_METHOD = 'LOCAL' AND PASSWORD_HASH IS NOT NULL AND GOOGLE_SUBJECT IS NULL) "
        "OR (AUTH_METHOD = 'GOOGLE' AND PASSWORD_HASH IS NULL))",
        "ALTER TABLE USERS DROP CONSTRAINT CHK_USERS_CREDENTIALS",
    ),
    SchemaChange(
        lambda conn: constraint_exists(conn, "UQ_USERS_GOOGLE_SUBJECT"),
        "ALTER TABLE USERS ADD CONSTRAINT UQ_USERS_GOOGLE_SUBJECT UNIQUE (GOOGLE_SUBJECT)",
        "ALTER TABLE USERS DROP CONSTRAINT UQ_USERS_GOOGLE_SUBJECT",
    ),
]


def _upgrade_auth_method(conn: Connection) -> None:
    for change in USERS_AUTH_METHOD:
        change.apply(conn)


def _rollback_auth_method(conn: Connection) -> None:
    for change in reversed(USERS_AUTH_METHOD):
        change.undo(conn)


# Step n takes the schema from version n - 1 to n; version 0 is the frozen
# 1.0.0 schema. Append only: never change or remove a released step.
UPGRADES: list[UpgradeStep] = [
    UpgradeStep(upgrade=_upgrade_auth_method, rollback=_rollback_auth_method),
]


class SchemaTooNewError(RuntimeError):
    """The database has steps this release does not know: it is newer."""

    def __init__(self, version: int, known: int) -> None:
        super().__init__(
            f"The database schema is at version {version}, but this release knows up "
            f"to {known}: run init-db from a release at least as new"
        )


def apply_upgrades(conn: Connection) -> None:
    """
    Apply the pending upgrade steps in order, after rolling back one left
    halfway. SchemaTooNewError when the database has steps this release
    does not know.
    """
    _finish_interrupted(conn)
    version = schema_version(conn)
    if version > len(UPGRADES):
        raise SchemaTooNewError(version, len(UPGRADES))
    for number in range(version + 1, len(UPGRADES) + 1):
        _apply(conn, number, UPGRADES[number - 1])


def schema_version(conn: Connection) -> int:
    """The last step applied; 0 for a database from before versions."""
    if not table_exists(conn, "SETTINGS"):
        return 0
    with conn.cursor() as cur:
        cur.execute("SELECT \"VALUE\" FROM SETTINGS WHERE NAME = 'SCHEMA_VERSION'")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def upgrades_pending(conn: Connection) -> bool:
    """True when the database is at an older version than this release's."""
    return schema_version(conn) < len(UPGRADES)


def _finish_interrupted(conn: Connection) -> None:
    """Roll back the step a crash or a failed rollback left halfway, if any."""
    with conn.cursor() as cur:
        cur.execute("SELECT \"VALUE\" FROM SETTINGS WHERE NAME = 'SCHEMA_UPGRADE_STEP'")
        row = cur.fetchone()
    if row is None:
        return
    number = int(row[0])
    if number > len(UPGRADES):
        raise SchemaTooNewError(number, len(UPGRADES))
    logger.warning("Upgrade step %d was left halfway: rolling it back", number)
    _roll_back(conn, number, UPGRADES[number - 1])


def _apply(conn: Connection, number: int, step: UpgradeStep) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', ?)",
            (str(number),),
        )
    conn.commit()
    logger.info("Applying upgrade step %d", number)
    try:
        step.upgrade(conn)
    except Exception:
        logger.exception("Upgrade step %d failed: rolling it back", number)
        if conn.is_active():
            conn.rollback()
        _roll_back(conn, number, step)
        raise
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE SETTINGS SET \"VALUE\" = ? WHERE NAME = 'SCHEMA_VERSION'",
            (str(number),),
        )
        cur.execute("DELETE FROM SETTINGS WHERE NAME = 'SCHEMA_UPGRADE_STEP'")
    conn.commit()
    logger.info("Upgrade step %d applied", number)


def _roll_back(conn: Connection, number: int, step: UpgradeStep) -> None:
    """Undo a step; if this fails, it stays marked and the next run tries again."""
    step.rollback(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM SETTINGS WHERE NAME = 'SCHEMA_UPGRADE_STEP'")
    conn.commit()
    logger.info("Upgrade step %d rolled back", number)
