"""Guarded schema changes: KeyedChange drops a key around its change."""

from collections.abc import Iterator

import pytest
from flask import Flask
from sqlalchemy import Connection, text

from bookshelf.db import get_engine
from bookshelf.db.catalog import column_type, constraint_exists
from bookshelf.db.changes import Key, KeyedChange, SchemaChange

# A keyed column retyped, both ways (Firebird retypes no column a key uses).
RETYPE = KeyedChange(
    SchemaChange(
        lambda conn: column_type(conn, "CODES", "CODE") == "VARCHAR(10)",
        "ALTER TABLE CODES ALTER COLUMN CODE TYPE VARCHAR(10)",
        "ALTER TABLE CODES ALTER COLUMN CODE TYPE CHAR(10)",
    ),
    (
        Key(
            "UQ_CODES_CODE",
            "ALTER TABLE CODES ADD CONSTRAINT UQ_CODES_CODE UNIQUE (CODE)",
            "ALTER TABLE CODES DROP CONSTRAINT UQ_CODES_CODE",
        ),
    ),
)


@pytest.fixture
def conn(empty_db_app: Flask) -> Iterator[Connection]:
    """A connection to a database with a CODES table, its CODE column keyed."""
    with empty_db_app.app_context(), get_engine().connect() as conn:
        conn.execute(
            text("CREATE TABLE CODES (CODE CHAR(10) CONSTRAINT UQ_CODES_CODE UNIQUE)")
        )
        conn.commit()
        yield conn


def test_apply_retypes_a_keyed_column(conn: Connection):
    RETYPE.apply(conn)
    RETYPE.apply(conn)  # a second time changes nothing more

    assert column_type(conn, "CODES", "CODE") == "VARCHAR(10)"
    assert constraint_exists(conn, "UQ_CODES_CODE")


def test_undo_retypes_a_keyed_column_back(conn: Connection):
    RETYPE.apply(conn)

    RETYPE.undo(conn)
    RETYPE.undo(conn)  # a second time changes nothing more

    assert column_type(conn, "CODES", "CODE") == "CHAR(10)"
    assert constraint_exists(conn, "UQ_CODES_CODE")
