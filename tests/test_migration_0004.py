"""Revision 0004: USERS.LAST_LOGIN_AT, when each user last logged in."""

from collections.abc import Callable, Iterator
from types import ModuleType
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import Flask
from raw_db import RawConnection, raw, table_columns

from bookshelf.db import get_engine, get_session
from bookshelf.db.schema import MIGRATIONS, init_db

REVISION: ModuleType = ScriptDirectory(str(MIGRATIONS)).get_revision("0004").module

EMAIL = "reader@example.com"
# USERS's columns at revision 0003.
V3_COLUMNS = [
    "ID",
    "EMAIL",
    "PASSWORD_HASH",
    "ROLE",
    "IS_LOCKED",
    "NAME",
    "SURNAME",
    "AUTH_METHOD",
    "GOOGLE_SUBJECT",
]


@pytest.fixture
def v3_conn(
    empty_db_app: Flask, migrations: Callable[..., None]
) -> Iterator[RawConnection]:
    """A connection to a database at revision 0003, holding a user."""
    migrations(last="0003")
    with empty_db_app.app_context():
        init_db(get_engine())
        conn = raw(get_session())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH) VALUES (?, ?, 'hash')",
                (uuid4(), EMAIL),
            )
        conn.commit()
        migrations()
        yield conn


def _refresh(conn: RawConnection) -> None:
    """End `conn`'s transaction, if any: its next one sees what others did."""
    if conn.is_active():
        conn.commit()


def _run_revision(apply: Callable[[], None]) -> None:
    """Run the revision's upgrade or downgrade as init_db does, then end its work."""
    with (
        get_engine().connect() as conn,
        Operations.context(MigrationContext.configure(conn)),
    ):
        apply()


def _emails(conn: RawConnection) -> list[object]:
    with conn.cursor() as cur:
        cur.execute("SELECT EMAIL FROM USERS")
        return [email for (email,) in cur.fetchall()]


def test_users_have_no_last_login_after_the_upgrade(v3_conn: RawConnection):
    init_db(get_engine())
    _refresh(v3_conn)

    assert table_columns(v3_conn, "USERS") == [*V3_COLUMNS, "LAST_LOGIN_AT"]
    with v3_conn.cursor() as cur:
        cur.execute("SELECT EMAIL, LAST_LOGIN_AT FROM USERS")
        assert cur.fetchall() == [(EMAIL, None)]


@pytest.mark.parametrize("applied", range(len(REVISION.CHANGES) + 1))
def test_rollback_after_failing_at_each_change(v3_conn: RawConnection, applied: int):
    """`upgrade` stopped after `applied` changes: downgrade restores revision 0003."""
    with get_engine().connect() as conn:
        for change in REVISION.CHANGES[:applied]:
            change.apply(conn)

    _run_revision(REVISION.downgrade)
    _run_revision(REVISION.downgrade)  # a second time changes nothing more
    _refresh(v3_conn)

    assert table_columns(v3_conn, "USERS") == V3_COLUMNS
    assert _emails(v3_conn) == [EMAIL]


def test_upgrade_runs_again(v3_conn: RawConnection):
    _run_revision(REVISION.upgrade)
    _run_revision(REVISION.upgrade)
    _refresh(v3_conn)

    assert table_columns(v3_conn, "USERS") == [*V3_COLUMNS, "LAST_LOGIN_AT"]
