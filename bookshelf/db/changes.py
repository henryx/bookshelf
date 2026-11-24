"""Guarded schema changes, the building blocks of the migrations' revisions."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, text

from bookshelf.config import DatabaseBackend
from bookshelf.db import backend_of
from bookshelf.db.catalog import constraint_exists

__all__ = ["Key", "KeyedChange", "SchemaChange", "Sql"]

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
    on its own (Firebird makes DDL usable only once committed). Without
    undo_sql the change stays when its revision is undone: one the previous
    release works with, that cannot be undone (Firebird narrows no column).
    """

    applied: Callable[[Connection], bool]
    apply_sql: Sql
    undo_sql: Sql | None

    def apply(self, conn: Connection) -> None:
        if not self.applied(conn):
            conn.execute(text(statement(conn, self.apply_sql)))
            conn.commit()

    def undo(self, conn: Connection) -> None:
        if self.undo_sql is not None and self.applied(conn):
            conn.execute(text(statement(conn, self.undo_sql)))
            conn.commit()


@dataclass(frozen=True)
class Key:
    """A table constraint KeyedChange drops and adds again: its name and DDL."""

    name: str
    add_sql: Sql
    drop_sql: Sql


@dataclass(frozen=True)
class KeyedChange:
    """
    A change Firebird refuses while a key uses the column (a type change of
    a column in a UNIQUE constraint): when `change` is still to be made (or
    undone), the `keys` are dropped first; whichever is missing is added
    again after. Every step is guarded, so either way runs again after
    failing halfway. While a key is dropped, the database does not enforce
    it: a duplicate written meanwhile makes adding it fail, and the change
    with it.
    """

    change: SchemaChange
    keys: tuple[Key, ...]

    def apply(self, conn: Connection) -> None:
        if not self.change.applied(conn):
            self._drop_keys(conn)
            self.change.apply(conn)
        self._add_keys(conn)

    def undo(self, conn: Connection) -> None:
        if self.change.undo_sql is not None and self.change.applied(conn):
            self._drop_keys(conn)
            self.change.undo(conn)
        self._add_keys(conn)

    def _drop_keys(self, conn: Connection) -> None:
        for key in self.keys:
            if constraint_exists(conn, key.name):
                conn.execute(text(statement(conn, key.drop_sql)))
                conn.commit()

    def _add_keys(self, conn: Connection) -> None:
        for key in self.keys:
            if not constraint_exists(conn, key.name):
                conn.execute(text(statement(conn, key.add_sql)))
                conn.commit()
