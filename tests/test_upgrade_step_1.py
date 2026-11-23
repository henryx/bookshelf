"""Upgrade step 1: how a user logs in, locally (with a password) or with Google."""

from uuid import uuid4

import pytest
from firebird.driver import Connection, DatabaseError
from flask import Flask

from bookshelf.db import get_db, upgrades
from bookshelf.db.schema import init_db
from bookshelf.db.upgrades import USERS_AUTH_METHOD

# USERS at version 0: its columns and constraints.
V0_COLUMNS = ["ID", "EMAIL", "PASSWORD_HASH", "ROLE", "IS_LOCKED", "NAME", "SURNAME"]
V0_CONSTRAINTS = {
    "PK_USERS",
    "UQ_USERS_EMAIL",
    "CHK_USERS_ROLE",
    "CHK_USERS_EMAIL_NORMALISED",
    "CHK_USERS_NAME",
    "CHK_USERS_SURNAME",
}


def _columns(conn: Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = 'USERS' ORDER BY RDB$FIELD_POSITION"
        )
        return [column for (column,) in cur.fetchall()]


def _constraints(conn: Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$CONSTRAINT_NAME) FROM RDB$RELATION_CONSTRAINTS "
            "WHERE RDB$RELATION_NAME = 'USERS' AND RDB$CONSTRAINT_TYPE <> 'NOT NULL'"
        )
        return {name for (name,) in cur.fetchall()}


def _password_hash_nullable(conn: Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT RDB$NULL_FLAG FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = 'USERS' AND RDB$FIELD_NAME = 'PASSWORD_HASH'"
        )
        (null_flag,) = cur.fetchone()
    return not null_flag


@pytest.fixture
def v0_conn(empty_db_app: Flask, monkeypatch: pytest.MonkeyPatch):
    """A connection to a database at version 0, holding a user."""
    monkeypatch.setattr(upgrades, "UPGRADES", [])
    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO USERS (ID, EMAIL, PASSWORD_HASH) "
                "VALUES (?, 'reader@example.com', 'hash')",
                (uuid4().bytes,),
            )
        conn.commit()
        monkeypatch.undo()
        yield conn


def test_existing_users_log_in_locally(v0_conn: Connection):
    init_db(v0_conn)

    with v0_conn.cursor() as cur:
        cur.execute(
            "SELECT EMAIL, PASSWORD_HASH, AUTH_METHOD, GOOGLE_SUBJECT FROM USERS"
        )
        assert cur.fetchall() == [("reader@example.com", "hash", "LOCAL", None)]
    assert _columns(v0_conn) == [*V0_COLUMNS, "AUTH_METHOD", "GOOGLE_SUBJECT"]
    assert _password_hash_nullable(v0_conn)
    assert _constraints(v0_conn) == V0_CONSTRAINTS | {
        "CHK_USERS_AUTH_METHOD",
        "CHK_USERS_CREDENTIALS",
        "UQ_USERS_GOOGLE_SUBJECT",
    }


@pytest.mark.parametrize("applied", range(len(USERS_AUTH_METHOD)))
def test_rollback_after_failing_at_each_change(v0_conn: Connection, applied: int):
    """`upgrade` stopped after `applied` changes: rollback restores version 0."""
    for change in USERS_AUTH_METHOD[:applied]:
        change.apply(v0_conn)

    upgrades.UPGRADES[0].rollback(v0_conn)
    upgrades.UPGRADES[0].rollback(v0_conn)  # a second time changes nothing more

    assert _columns(v0_conn) == V0_COLUMNS
    assert not _password_hash_nullable(v0_conn)
    assert _constraints(v0_conn) == V0_CONSTRAINTS
    with v0_conn.cursor() as cur:
        cur.execute("SELECT EMAIL, PASSWORD_HASH FROM USERS")
        assert cur.fetchall() == [("reader@example.com", "hash")]


def test_upgrade_run_twice_changes_nothing_more(v0_conn: Connection):
    upgrades.UPGRADES[0].upgrade(v0_conn)
    upgrades.UPGRADES[0].upgrade(v0_conn)

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
    v0_conn: Connection,
    method: str,
    password_hash: str | None,
    subject: str | None,
    allowed: bool,
):
    init_db(v0_conn)
    try:
        with v0_conn.cursor() as cur:
            cur.execute(_INSERT, (uuid4().bytes, method, password_hash, subject))
        v0_conn.commit()
        inserted = True
    except DatabaseError:  # a check constraint refusing the row
        v0_conn.rollback()
        inserted = False

    assert inserted is allowed
