"""Guarded schema changes, the building blocks of the migrations' revisions."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, text

from bookshelf.config import DatabaseBackend
from bookshelf.db import backend_of

__all__ = ["SchemaChange", "Sql"]

# A statement: the same on every backend, or each backend's own.
type Sql = str | Mapping[DatabaseBackend, str]


def statement(conn: Connection, sql: Sql) -> str:
    """The statement `sql` is on the backend `conn` talks to."""
    return sql if isinstance(sql, str) else sql[backend_of(conn)]


@dataclass(frozen=True)
class SchemaChange:
    """
    One change of a revision, rerunnable both ways: apply_sql runs unless
    `applied` says it is already made, undo_sql only if it is. Each commits
    on its own (Firebird makes DDL usable only once committed).
    """

    applied: Callable[[Connection], bool]
    apply_sql: Sql
    undo_sql: Sql

    def apply(self, conn: Connection) -> None:
        if not self.applied(conn):
            conn.execute(text(statement(conn, self.apply_sql)))
            conn.commit()

    def undo(self, conn: Connection) -> None:
        if self.applied(conn):
            conn.execute(text(statement(conn, self.undo_sql)))
            conn.commit()
