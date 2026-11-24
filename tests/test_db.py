from typing import cast
from uuid import uuid4

import pytest
from firebird.driver import DatabaseError
from flask import Flask
from psycopg.errors import lookup
from raw_db import BACKEND, RawConnection, raw
from sqlalchemy import insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf import create_app
from bookshelf.config import DatabaseBackend
from bookshelf.db import (
    close_pool,
    execute_change,
    get_session,
    is_concurrent_delete,
    is_foreign_key_violation,
    is_unique_violation,
    is_update_conflict,
    read_committed,
    retry_after_concurrent_delete,
)
from bookshelf.db.orm import UserRow


def _one(session: Session | RawConnection, sql: str, *params: object) -> object:
    conn = session if isinstance(session, RawConnection) else raw(session)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    assert row is not None
    return row[0]


def _attachment_id(session: Session) -> int:
    """The server's ID of the session's connection."""
    sql = {
        DatabaseBackend.FIREBIRD: "SELECT CURRENT_CONNECTION FROM RDB$DATABASE",
        DatabaseBackend.POSTGRESQL: "SELECT pg_backend_pid()",
    }
    return cast(int, _one(session, sql[BACKEND]))


def _kill(conn: RawConnection, attachment_id: int) -> None:
    """End another connection, as a server restart or an admin would."""
    sql = {
        DatabaseBackend.FIREBIRD: (
            "DELETE FROM MON$ATTACHMENTS WHERE MON$ATTACHMENT_ID = ?"
        ),
        DatabaseBackend.POSTGRESQL: "SELECT pg_terminate_backend(?)",
    }
    with conn.cursor() as cur:
        cur.execute(sql[BACKEND], (attachment_id,))
    conn.commit()


def _read_committed(session: Session) -> bool:
    """Whether the session's transaction is read committed."""
    if BACKEND is DatabaseBackend.FIREBIRD:
        # MON$ISOLATION_MODE 2 and 3 are read committed.
        mode = _one(
            session,
            "SELECT MON$ISOLATION_MODE FROM MON$TRANSACTIONS "
            "WHERE MON$TRANSACTION_ID = CURRENT_TRANSACTION",
        )
        return mode in (2, 3)
    return _one(session, "SHOW transaction_isolation") == "read committed"


def _user_count(session: Session) -> int:
    return cast(int, _one(session, "SELECT COUNT(*) FROM USERS"))


def _add_user(conn: RawConnection, email: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH) VALUES (?, ?, ?)",
            (uuid4(), email, "hash"),
        )


def test_connection_is_reused_across_app_contexts(app: Flask):
    with app.app_context():
        first = _attachment_id(get_session())
    with app.app_context():
        second = _attachment_id(get_session())

    assert first == second


def test_uncommitted_work_is_rolled_back_when_returned(db_app: Flask):
    with db_app.app_context():
        _add_user(raw(get_session()), "uncommitted@example.com")
    with db_app.app_context():
        assert _user_count(get_session()) == 0


def test_closed_connection_is_not_reused(app: Flask):
    with app.app_context():
        session = get_session()
        first = _attachment_id(session)
        dbapi_connection = session.connection().connection.dbapi_connection
        assert dbapi_connection is not None
        # Back in the pool first, as a connection the server closes while idle.
        session.rollback()
        dbapi_connection.close()
    with app.app_context():
        assert _attachment_id(get_session()) != first


def test_connection_killed_while_idle_is_replaced(
    app: Flask, other_conn: RawConnection
):
    with app.app_context():
        killed = _attachment_id(get_session())

    _kill(other_conn, killed)

    with app.app_context():
        assert _attachment_id(get_session()) != killed


def test_idle_connections_are_capped_at_pool_size():
    app = create_app({"TESTING": True, "DATABASE_POOL_SIZE": 1})
    try:
        with app.app_context():
            outer = _attachment_id(get_session())
            with app.app_context():
                inner = _attachment_id(get_session())
        with app.app_context():
            reused = _attachment_id(get_session())
            with app.app_context():
                fresh = _attachment_id(get_session())
    finally:
        close_pool(app)

    assert reused in {outer, inner}
    assert fresh not in {outer, inner}


# Transactions


def test_transactions_are_snapshots_by_default(
    db_app: Flask, other_conn: RawConnection
):
    with db_app.app_context():
        session = get_session()
        assert _user_count(session) == 0
        _add_user(other_conn, "committed@example.com")
        other_conn.commit()

        assert _user_count(session) == 0


def test_read_committed_sees_what_others_committed(
    db_app: Flask, other_conn: RawConnection
):
    with db_app.app_context():
        session = get_session()
        assert _user_count(session) == 0
        _add_user(other_conn, "committed@example.com")
        other_conn.commit()

        with read_committed(session):
            assert _read_committed(session)
            assert _user_count(session) == 1


def test_read_committed_ends_with_its_transaction(db_app: Flask):
    with db_app.app_context():
        session = get_session()
        with read_committed(session):
            session.commit()

        assert not _read_committed(session)


def test_read_committed_rolls_back_when_its_block_raises(db_app: Flask):
    with db_app.app_context():
        session = get_session()
        with pytest.raises(RuntimeError), read_committed(session):
            _add_user(raw(session), "rolled.back@example.com")
            raise RuntimeError("failed")

        assert session.scalars(select(UserRow)).all() == []


def test_a_change_with_returning_is_refused(db_app: Flask):
    """Its rows come from session.scalars: execute_change has no cursor result."""
    statement = (
        insert(UserRow)
        .values(id=uuid4(), email="returning@example.com", password_hash="hash")
        .returning(UserRow.id)
    )
    with db_app.app_context(), pytest.raises(TypeError, match="Not a change"):
        execute_change(get_session(), statement)


# Errors


def _error(gds_code: int, sqlstate: str) -> DBAPIError:
    """A database error, as SQLAlchemy raises it from the backend's driver."""
    match BACKEND:
        case DatabaseBackend.FIREBIRD:
            error = DatabaseError(f"error {gds_code}")
            error.gds_codes = (gds_code,)
            return DBAPIError("UPDATE", None, error)
        case DatabaseBackend.POSTGRESQL:
            return DBAPIError("UPDATE", None, lookup(sqlstate)(f"error {sqlstate}"))


def _concurrent_delete() -> DBAPIError:
    return _error(335544382, "40001")


def _unique_violation() -> DBAPIError:
    return _error(335544665, "23505")


def test_errors_are_told_apart():
    assert is_concurrent_delete(_concurrent_delete())
    assert is_unique_violation(_unique_violation())
    assert not is_unique_violation(_concurrent_delete())
    assert is_foreign_key_violation(_error(335544466, "23503"))
    assert is_update_conflict(_error(335544451, "40001"))
    assert not is_unique_violation(RuntimeError("not a database error"))


def test_run_again_after_a_concurrent_delete():
    calls: list[int] = []

    def run() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise _concurrent_delete()
        return "done"

    assert retry_after_concurrent_delete(run) == "done"
    assert len(calls) == 2


def test_run_again_only_once():
    calls: list[int] = []

    def run() -> None:
        calls.append(1)
        raise _concurrent_delete()

    with pytest.raises(DBAPIError):
        retry_after_concurrent_delete(run)
    assert len(calls) == 2


def test_other_database_errors_are_not_run_again():
    calls: list[int] = []

    def run() -> None:
        calls.append(1)
        raise _unique_violation()

    with pytest.raises(DBAPIError):
        retry_after_concurrent_delete(run)
    assert calls == [1]
