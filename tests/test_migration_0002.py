"""Revision 0002: how a user logs in, locally (with a password) or with Google."""

from collections.abc import Callable, Iterator
from types import ModuleType
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import Flask
from raw_db import RawConnection, raw, table_columns, table_constraints
from sqlalchemy.exc import DBAPIError

from bookshelf.db import get_engine, get_session
from bookshelf.db.catalog import column_nullable
from bookshelf.db.schema import MIGRATIONS, init_db

REVISION: ModuleType = ScriptDirectory(str(MIGRATIONS)).get_revision("0002").module

# USERS at revision 0001: its columns and constraints.
V0_COLUMNS = ["ID", "EMAIL", "PASSWORD_HASH", "ROLE", "IS_LOCKED", "NAME", "SURNAME"]
V0_CONSTRAINTS = {
    "PK_USERS",
    "UQ_USERS_EMAIL",
    "CHK_USERS_ROLE",
    "CHK_USERS_EMAIL_NORMALISED",
    "CHK_USERS_NAME",
    "CHK_USERS_SURNAME",
}


def _columns(conn: RawConnection) -> list[str]:
    return table_columns(conn, "USERS")


def _constraints(conn: RawConnection) -> set[str]:
    return table_constraints(conn, "USERS")


def _password_hash_nullable() -> bool:
    session = get_session()
    session.rollback()
    return column_nullable(session.connection(), "USERS", "PASSWORD_HASH")


@pytest.fixture
def v0_conn(
    empty_db_app: Flask, migrations: Callable[..., None]
) -> Iterator[RawConnection]:
    """
    A connection to a database at revision 0001, holding a user; init_db then
    knows the revisions up to 0002 only, so later ones do not change what
    these tests see.
    """
    migrations(last="0001")
    with empty_db_app.app_context():
        init_db(get_engine())
        conn = raw(get_session())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH) "
                "VALUES (?, 'reader@example.com', 'hash')",
                (uuid4(),),
            )
        conn.commit()
        migrations(last="0002")
        yield conn


def _refresh(conn: RawConnection) -> None:
    """End `conn`'s transaction, if any: its next one sees what others did."""
    if conn.is_active():
        conn.commit()


def _upgrade_to_0002(conn: RawConnection) -> None:
    """init_db (up to 0002), then a new transaction of `conn`, which sees its changes."""
    init_db(get_engine())
    _refresh(conn)


def _run_revision(apply: Callable[[], None]) -> None:
    """Run the revision's upgrade or downgrade as init_db does, then end its work."""
    with (
        get_engine().connect() as conn,
        Operations.context(MigrationContext.configure(conn)),
    ):
        apply()


def test_existing_users_log_in_locally(v0_conn: RawConnection):
    _upgrade_to_0002(v0_conn)

    with v0_conn.cursor() as cur:
        cur.execute(
            "SELECT EMAIL, PASSWORD_HASH, AUTH_METHOD, GOOGLE_SUBJECT FROM USERS"
        )
        assert cur.fetchall() == [("reader@example.com", "hash", "LOCAL", None)]
    assert _columns(v0_conn) == [*V0_COLUMNS, "AUTH_METHOD", "GOOGLE_SUBJECT"]
    assert _password_hash_nullable()
    assert _constraints(v0_conn) == V0_CONSTRAINTS | {
        "CHK_USERS_AUTH_METHOD",
        "CHK_USERS_CREDENTIALS",
        "UQ_USERS_GOOGLE_SUBJECT",
    }


@pytest.mark.parametrize("applied", range(len(REVISION.CHANGES)))
def test_rollback_after_failing_at_each_change(v0_conn: RawConnection, applied: int):
    """`upgrade` stopped after `applied` changes: downgrade restores revision 0001."""
    with get_engine().connect() as conn:
        for change in REVISION.CHANGES[:applied]:
            change.apply(conn)

    _run_revision(REVISION.downgrade)
    _run_revision(REVISION.downgrade)  # a second time changes nothing more
    _refresh(v0_conn)

    assert _columns(v0_conn) == V0_COLUMNS
    assert not _password_hash_nullable()
    assert _constraints(v0_conn) == V0_CONSTRAINTS
    with v0_conn.cursor() as cur:
        cur.execute("SELECT EMAIL, PASSWORD_HASH FROM USERS")
        assert cur.fetchall() == [("reader@example.com", "hash")]


def test_upgrade_run_twice_changes_nothing_more(v0_conn: RawConnection):
    _run_revision(REVISION.upgrade)
    _run_revision(REVISION.upgrade)
    _refresh(v0_conn)

    assert _columns(v0_conn) == [*V0_COLUMNS, "AUTH_METHOD", "GOOGLE_SUBJECT"]


_INSERT = (
    "INSERT INTO USERS (ID, EMAIL, AUTH_METHOD, PASSWORD_HASH, GOOGLE_SUBJECT) "
    "VALUES (?, 'new@example.com', ?, ?, ?)"
)


@pytest.mark.parametrize(
    ("method", "password_hash", "subject", "allowed"),
    [
        ("LOCAL", "hash", None, True),
        ("GOOGLE", None, None, True),  # not logged in with Google yet
        ("GOOGLE", None, "subject", True),
        ("LOCAL", None, None, False),  # a local user needs a password
        ("GOOGLE", "hash", None, False),  # a Google user has none
        ("LOCAL", "hash", "subject", False),  # only a Google user has a subject
        ("OTHER", "hash", None, False),
    ],
)
def test_credentials_match_the_auth_method(
    v0_conn: RawConnection,
    method: str,
    password_hash: str | None,
    subject: str | None,
    allowed: bool,
):
    _upgrade_to_0002(v0_conn)
    try:
        with v0_conn.cursor() as cur:
            cur.execute(_INSERT, (uuid4(), method, password_hash, subject))
        _refresh(v0_conn)
        inserted = True
    except DBAPIError:  # a check constraint refusing the row
        v0_conn.rollback()
        inserted = False

    assert inserted is allowed
