from uuid import uuid4

import pytest
from firebird.driver import Connection, DatabaseError, connect
from flask import Flask
from raw_db import raw
from sqlalchemy import insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf import create_app
from bookshelf.db import (
    close_pool,
    execute_change,
    get_session,
    has_gds_code,
    read_committed,
    retry_after_concurrent_delete,
)
from bookshelf.db.orm import UserRow


def _attachment_id(session: Session) -> int:
    with raw(session).cursor() as cur:
        cur.execute("SELECT CURRENT_CONNECTION FROM RDB$DATABASE")
        (attachment_id,) = cur.fetchone()
    return attachment_id


def _isolation(session: Session) -> int:
    """The current transaction's isolation (MON$ISOLATION_MODE: 2 is read committed)."""
    with raw(session).cursor() as cur:
        cur.execute(
            "SELECT MON$ISOLATION_MODE FROM MON$TRANSACTIONS "
            "WHERE MON$TRANSACTION_ID = CURRENT_TRANSACTION"
        )
        (mode,) = cur.fetchone()
    return mode


def _user_count(session: Session) -> int:
    with raw(session).cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM USERS")
        (count,) = cur.fetchone()
    return count


def _add_user(conn: Connection, email: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH) VALUES (?, ?, ?)",
            (uuid4().bytes, email, "hash"),
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
        conn = raw(session)
        conn.rollback()
        conn.close()
    with app.app_context():
        assert _attachment_id(get_session()) != first


def test_connection_killed_while_idle_is_replaced(app: Flask):
    with app.app_context():
        killed = _attachment_id(get_session())

    cfg = app.config
    with connect(
        cfg["DATABASE_DSN"],
        user=cfg["DATABASE_USER"],
        password=cfg["DATABASE_PASSWORD"],
    ) as killer:
        with killer.cursor() as cur:
            cur.execute(
                "DELETE FROM MON$ATTACHMENTS WHERE MON$ATTACHMENT_ID = ?", (killed,)
            )
        killer.commit()

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


def test_transactions_are_snapshots_by_default(db_app: Flask, other_conn: Connection):
    with db_app.app_context():
        session = get_session()
        assert _user_count(session) == 0
        _add_user(other_conn, "committed@example.com")
        other_conn.commit()

        assert _user_count(session) == 0


def test_read_committed_sees_what_others_committed(
    db_app: Flask, other_conn: Connection
):
    with db_app.app_context():
        session = get_session()
        assert _user_count(session) == 0
        _add_user(other_conn, "committed@example.com")
        other_conn.commit()

        with read_committed(session):
            assert _isolation(session) == 2
            assert _user_count(session) == 1


def test_read_committed_ends_with_its_transaction(db_app: Flask):
    with db_app.app_context():
        session = get_session()
        with read_committed(session):
            session.commit()

        assert _isolation(session) != 2


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

ERASED_RECORD = 335544382


def _database_error(code: int) -> DBAPIError:
    error = DatabaseError(f"error {code}")
    error.gds_codes = (code,)
    return DBAPIError("UPDATE", None, error)


def test_gds_codes_are_read_from_the_driver_error():
    assert has_gds_code(_database_error(ERASED_RECORD), ERASED_RECORD)
    assert not has_gds_code(_database_error(ERASED_RECORD), 1)
    assert not has_gds_code(RuntimeError("not a database error"), ERASED_RECORD)


def test_run_again_after_a_concurrent_delete():
    calls: list[int] = []

    def run() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise _database_error(ERASED_RECORD)
        return "done"

    assert retry_after_concurrent_delete(run) == "done"
    assert len(calls) == 2


def test_run_again_only_once():
    calls: list[int] = []

    def run() -> None:
        calls.append(1)
        raise _database_error(ERASED_RECORD)

    with pytest.raises(DBAPIError):
        retry_after_concurrent_delete(run)
    assert len(calls) == 2


def test_other_database_errors_are_not_run_again():
    calls: list[int] = []

    def run() -> None:
        calls.append(1)
        raise _database_error(335544665)

    with pytest.raises(DBAPIError):
        retry_after_concurrent_delete(run)
    assert calls == [1]
