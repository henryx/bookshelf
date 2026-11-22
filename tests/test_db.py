import logging
from typing import cast
from uuid import uuid4

import pytest
from firebird.driver import Connection, DatabaseError, connect
from flask import Flask

from bookshelf import create_app
from bookshelf.db import ConnectionPool, close_pool, get_db


def _attachment_id(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT CURRENT_CONNECTION FROM RDB$DATABASE")
        (attachment_id,) = cur.fetchone()
    return attachment_id


def _user_count(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM USERS")
        (count,) = cur.fetchone()
    return count


def test_connection_is_reused_across_app_contexts(app: Flask):
    with app.app_context():
        first = _attachment_id(get_db())
    with app.app_context():
        second = _attachment_id(get_db())

    assert first == second


def test_uncommitted_work_is_rolled_back_when_returned(db_app: Flask):
    with db_app.app_context(), get_db().cursor() as cur:
        cur.execute(
            "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH) VALUES (?, ?, ?)",
            (uuid4().bytes, "uncommitted@example.com", "hash"),
        )
    with db_app.app_context():
        assert _user_count(get_db()) == 0


def test_closed_connection_is_not_reused(app: Flask):
    with app.app_context():
        conn = get_db()
        first = _attachment_id(conn)
        conn.rollback()
        conn.close()
    with app.app_context():
        assert _attachment_id(get_db()) != first


def test_connection_killed_while_idle_is_replaced(app: Flask):
    with app.app_context():
        killed = _attachment_id(get_db())

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
        assert _attachment_id(get_db()) != killed


def test_idle_connections_are_capped_at_pool_size():
    app = create_app({"TESTING": True, "DATABASE_POOL_SIZE": 1})
    try:
        with app.app_context():
            outer = _attachment_id(get_db())
            with app.app_context():
                inner = _attachment_id(get_db())
        with app.app_context():
            reused = _attachment_id(get_db())
            with app.app_context():
                fresh = _attachment_id(get_db())
    finally:
        close_pool(app)

    assert reused in {outer, inner}
    assert fresh not in {outer, inner}


class _BrokenConnection:
    """A connection whose transaction cannot be rolled back."""

    def __init__(self, *, close_fails: bool = False) -> None:
        self.close_fails = close_fails
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def is_active(self) -> bool:
        return True

    def rollback(self) -> None:
        raise DatabaseError("rollback failed")

    def close(self) -> None:
        if self.close_fails:
            raise DatabaseError("close failed")
        self.closed = True


def test_connection_that_fails_rollback_is_closed_not_reused():
    broken = _BrokenConnection()
    fresh = cast(Connection, object())
    pool = ConnectionPool(lambda: fresh, size=1)

    pool.release(cast(Connection, broken))

    assert broken.closed
    assert pool.acquire() is fresh


def test_error_closing_a_discarded_connection_is_logged(
    caplog: pytest.LogCaptureFixture,
):
    pool = ConnectionPool(lambda: cast(Connection, object()), size=1)

    with caplog.at_level(logging.WARNING, logger="bookshelf.db"):
        pool.release(cast(Connection, _BrokenConnection(close_fails=True)))

    assert caplog.messages == [
        "Discarding connection that failed rollback",
        "Error closing database connection",
    ]
