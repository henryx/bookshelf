"""Revision 0003: BOOKS.ISBN takes serials' codes (977…, with an add-on) too."""

from collections.abc import Callable, Iterator
from types import ModuleType
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import Flask
from raw_db import RawConnection, raw, table_constraints
from sqlalchemy.exc import DBAPIError

from bookshelf.db import get_engine, get_session
from bookshelf.db.catalog import column_type
from bookshelf.db.schema import MIGRATIONS, init_db

REVISION: ModuleType = ScriptDirectory(str(MIGRATIONS)).get_revision("0003").module

DUNE_ISBN = "9780441172719"
# BOOKS's constraints at revision 0002.
V2_CONSTRAINTS = {"PK_BOOKS", "UQ_BOOKS_ISBN", "CHK_BOOKS_ISBN", "CHK_BOOKS_TITLE"}


@pytest.fixture
def v2_conn(
    empty_db_app: Flask, migrations: Callable[..., None]
) -> Iterator[RawConnection]:
    """A connection to a database at revision 0002, holding a book with an ISBN."""
    migrations(last="0002")
    with empty_db_app.app_context():
        init_db(get_engine())
        conn = raw(get_session())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO BOOKS (ID, TITLE, ISBN) VALUES (?, 'Dune', ?)",
                (uuid4(), DUNE_ISBN),
            )
        conn.commit()
        migrations()
        yield conn


def _refresh(conn: RawConnection) -> None:
    """End `conn`'s transaction, if any: its next one sees what others did."""
    if conn.is_active():
        conn.commit()


def _isbn_type() -> str | None:
    session = get_session()
    session.rollback()
    return column_type(session.connection(), "BOOKS", "ISBN")


def _run_revision(apply: Callable[[], None]) -> None:
    """Run the revision's upgrade or downgrade as init_db does, then end its work."""
    with (
        get_engine().connect() as conn,
        Operations.context(MigrationContext.configure(conn)),
    ):
        apply()


def _add(conn: RawConnection, isbn: str) -> bool:
    """Add a book with this code; False if the database refuses it."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO BOOKS (ID, TITLE, ISBN) VALUES (?, 'A serial', ?)",
                (uuid4(), isbn),
            )
        conn.commit()
    except DBAPIError:
        conn.rollback()
        return False
    return True


def _isbns(conn: RawConnection) -> list[object]:
    with conn.cursor() as cur:
        cur.execute("SELECT ISBN FROM BOOKS ORDER BY ISBN")
        return [isbn for (isbn,) in cur.fetchall()]


def test_serials_codes_fit_after_the_upgrade(v2_conn: RawConnection):
    init_db(get_engine())
    _refresh(v2_conn)

    assert _isbn_type() == "VARCHAR(18)"
    assert table_constraints(v2_conn, "BOOKS") == (
        V2_CONSTRAINTS - {"CHK_BOOKS_ISBN"} | {"CHK_BOOKS_ISBN_CODE"}
    )
    assert _isbns(v2_conn) == [DUNE_ISBN]
    assert _add(v2_conn, "9770317847001")
    assert _add(v2_conn, "977031784700105")
    assert _add(v2_conn, "977031784700112345")
    assert not _add(v2_conn, "9770317847001123")
    assert not _add(v2_conn, "1234567890123")


@pytest.mark.parametrize("applied", range(len(REVISION.CHANGES)))
def test_rollback_after_failing_at_each_change(v2_conn: RawConnection, applied: int):
    """
    `upgrade` stopped after `applied` changes: downgrade restores revision
    0002's constraints and data. A widened column stays wide: Firebird
    narrows none, and 0002's code works with it.
    """
    with get_engine().connect() as conn:
        for change in REVISION.CHANGES[:applied]:
            change.apply(conn)

    _run_revision(REVISION.downgrade)
    _run_revision(REVISION.downgrade)  # a second time changes nothing more
    _refresh(v2_conn)

    assert _isbn_type() == ("CHAR(13)" if applied < 2 else "VARCHAR(18)")
    assert table_constraints(v2_conn, "BOOKS") == V2_CONSTRAINTS
    assert _isbns(v2_conn) == [DUNE_ISBN]
    assert not _add(v2_conn, "977031784700105")


def test_upgrade_run_twice_changes_nothing_more(v2_conn: RawConnection):
    _run_revision(REVISION.upgrade)
    _run_revision(REVISION.upgrade)
    _refresh(v2_conn)

    assert _isbn_type() == "VARCHAR(18)"
    assert "CHK_BOOKS_ISBN_CODE" in table_constraints(v2_conn, "BOOKS")
