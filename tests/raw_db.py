"""
The tests' own SQL: setting up rows and checking what is stored, run on
either database through SQLAlchemy, with a cursor's interface. Statements
take `?` parameters, in order; UUIDs are passed and returned as UUID.
"""

import os
import re
from collections.abc import Sequence
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import Connection, CursorResult, Result, bindparam, text
from sqlalchemy.orm import Session
from sqlalchemy.types import NullType

from bookshelf.config import DatabaseBackend
from bookshelf.db.orm import UUIDType

__all__ = [
    "BACKEND",
    "RawConnection",
    "RawCursor",
    "raw",
    "table_columns",
    "table_constraints",
]

# The database the tests run on: TEST_DB_BACKEND (tox: py3 Firebird,
# py3-postgres PostgreSQL).
BACKEND = DatabaseBackend(os.environ.get("TEST_DB_BACKEND", "firebird"))

# A `?` parameter outside quoted text.
_PARAMETER = re.compile(r"""'[^']*'|"[^"]*"|\?""")

type Row = tuple[object, ...]


def _named(sql: str) -> tuple[str, int]:
    """`sql` with its `?` parameters named :p0, :p1, …, and how many there are."""
    count = 0

    def name(match: re.Match[str]) -> str:
        nonlocal count
        if match.group() != "?":
            return match.group()
        count += 1
        return f":p{count - 1}"

    return _PARAMETER.sub(name, sql), count


def _value(value: object) -> object:
    """A stored UUID as UUID: Firebird gives its 16 bytes."""
    if isinstance(value, bytes) and len(value) == 16:
        return UUID(bytes=value)
    return value


class RawCursor:
    def __init__(self, conn: Session | Connection) -> None:
        self._conn = conn
        self._result: Result[object] | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._result = None

    def execute(self, sql: str, params: Sequence[object] = ()) -> RawCursor:
        named, count = _named(sql)
        if count != len(params):
            raise ValueError(f"{count} parameters in {sql!r}, {len(params)} given")
        statement = text(named).bindparams(
            *(
                # Others untyped, to the driver as they are: a typed string
                # would be cast as VARCHAR with no length on Firebird.
                bindparam(f"p{i}", value, type_=UUIDType())
                if isinstance(value, UUID)
                else bindparam(f"p{i}", value, type_=NullType())
                for i, value in enumerate(params)
            )
        )
        self._result = self._conn.execute(
            statement, execution_options={"preserve_rowcount": True}
        )
        return self

    def fetchone(self) -> Row | None:
        row = self._rows().fetchone()
        return None if row is None else tuple(_value(v) for v in row)

    def fetchall(self) -> list[Row]:
        return [tuple(_value(v) for v in row) for row in self._rows()]

    @property
    def affected_rows(self) -> int:
        result = self._result
        return result.rowcount if isinstance(result, CursorResult) else -1

    def _rows(self) -> Result[object]:
        if self._result is None:
            raise RuntimeError("No statement executed")
        return self._result


class RawConnection:
    """A session's or connection's transaction, with a DB-API connection's interface."""

    def __init__(self, conn: Session | Connection) -> None:
        self._conn = conn

    def cursor(self) -> RawCursor:
        return RawCursor(self._conn)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def is_active(self) -> bool:
        return self._conn.in_transaction()


def raw(session: Session) -> RawConnection:
    """`session`'s transaction, for the tests' own SQL; session.commit() commits it too."""
    return RawConnection(session)


def table_columns(conn: RawConnection, table: str) -> list[str]:
    """A table's columns, in order, upper case as the DDL writes them."""
    sql = {
        DatabaseBackend.FIREBIRD: (
            "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? ORDER BY RDB$FIELD_POSITION"
        ),
        DatabaseBackend.POSTGRESQL: (
            "SELECT UPPER(column_name) FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = LOWER(?) "
            "ORDER BY ordinal_position"
        ),
    }
    with conn.cursor() as cur:
        cur.execute(sql[BACKEND], (table,))
        return [str(column) for (column,) in cur.fetchall()]


def table_constraints(conn: RawConnection, table: str) -> set[str]:
    """A table's named constraints (Firebird's own NOT NULL ones aside)."""
    sql = {
        DatabaseBackend.FIREBIRD: (
            "SELECT TRIM(RDB$CONSTRAINT_NAME) FROM RDB$RELATION_CONSTRAINTS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$CONSTRAINT_TYPE <> 'NOT NULL'"
        ),
        DatabaseBackend.POSTGRESQL: (
            "SELECT UPPER(c.conname) FROM pg_catalog.pg_constraint c "
            "JOIN pg_catalog.pg_class t ON t.oid = c.conrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = current_schema() AND t.relname = LOWER(?) "
            "AND c.contype IN ('p', 'u', 'c', 'f')"
        ),
    }
    with conn.cursor() as cur:
        cur.execute(sql[BACKEND], (table,))
        return {str(name) for (name,) in cur.fetchall()}
