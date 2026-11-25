"""Revision 0005: COLLECTION_BOOKS.SCORE, each user's score of their books."""

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
from bookshelf.db.schema import init_db
from bookshelf.db.schema.revisions import MIGRATIONS

REVISION: ModuleType = ScriptDirectory(str(MIGRATIONS)).get_revision("0005").module

# COLLECTION_BOOKS's columns and constraints at revision 0004.
V4_COLUMNS = ["USER_ID", "BOOK_ID", "COPIES", "IS_READ"]
V4_CONSTRAINTS = {
    "PK_COLLECTION_BOOKS",
    "CHK_COLLECTION_BOOKS_COPIES",
    "FK_COLLECTION_BOOKS_USER",
    "FK_COLLECTION_BOOKS_BOOK",
}


@pytest.fixture
def v4_conn(
    empty_db_app: Flask, migrations: Callable[..., None]
) -> Iterator[RawConnection]:
    """
    A connection to a database at revision 0004, with a book in a user's
    collection; init_db then knows the revisions up to 0005 only, so later
    ones do not change what these tests see.
    """
    migrations(last="0004")
    with empty_db_app.app_context():
        init_db(get_engine())
        conn = raw(get_session())
        user_id, book_id = uuid4(), uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH) "
                "VALUES (?, 'reader@example.com', 'hash')",
                (user_id,),
            )
            cur.execute("INSERT INTO BOOKS (ID, TITLE) VALUES (?, 'Dune')", (book_id,))
            cur.execute(
                "INSERT INTO COLLECTION_BOOKS (USER_ID, BOOK_ID) VALUES (?, ?)",
                (user_id, book_id),
            )
        conn.commit()
        migrations(last="0005")
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


def _entries(conn: RawConnection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT BOOK_ID FROM COLLECTION_BOOKS")
        return len(cur.fetchall())


def _set_score(conn: RawConnection, score: int) -> bool:
    """Score the collection's book; False if the database refuses it."""
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE COLLECTION_BOOKS SET SCORE = ?", (score,))
        conn.commit()
    except DBAPIError:
        conn.rollback()
        return False
    return True


def test_books_are_unscored_after_the_upgrade(v4_conn: RawConnection):
    init_db(get_engine())
    _refresh(v4_conn)

    assert table_columns(v4_conn, "COLLECTION_BOOKS") == [*V4_COLUMNS, "SCORE"]
    assert table_constraints(v4_conn, "COLLECTION_BOOKS") == (
        V4_CONSTRAINTS | {"CHK_COLLECTION_BOOKS_SCORE"}
    )
    with v4_conn.cursor() as cur:
        cur.execute("SELECT SCORE FROM COLLECTION_BOOKS")
        assert cur.fetchall() == [(None,)]
    assert _set_score(v4_conn, 5)
    assert not _set_score(v4_conn, 6)
    assert not _set_score(v4_conn, 0)


@pytest.mark.parametrize("applied", range(len(REVISION.CHANGES) + 1))
def test_rollback_after_failing_at_each_change(v4_conn: RawConnection, applied: int):
    """`upgrade` stopped after `applied` changes: downgrade restores revision 0004."""
    with get_engine().connect() as conn:
        for change in REVISION.CHANGES[:applied]:
            change.apply(conn)

    _run_revision(REVISION.downgrade)
    _run_revision(REVISION.downgrade)  # a second time changes nothing more
    _refresh(v4_conn)

    assert table_columns(v4_conn, "COLLECTION_BOOKS") == V4_COLUMNS
    assert table_constraints(v4_conn, "COLLECTION_BOOKS") == V4_CONSTRAINTS
    assert _entries(v4_conn) == 1


def test_upgrade_runs_again(v4_conn: RawConnection):
    _run_revision(REVISION.upgrade)
    _run_revision(REVISION.upgrade)
    _refresh(v4_conn)

    assert table_columns(v4_conn, "COLLECTION_BOOKS") == [*V4_COLUMNS, "SCORE"]
    assert "CHK_COLLECTION_BOOKS_SCORE" in table_constraints(
        v4_conn, "COLLECTION_BOOKS"
    )
