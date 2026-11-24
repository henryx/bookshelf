"""Guarded schema changes, the building blocks of the migrations' revisions."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Connection, text

__all__ = ["SchemaChange"]


@dataclass(frozen=True)
class SchemaChange:
    """
    One change of a revision, rerunnable both ways: apply_sql runs unless
    `applied` says it is already made, undo_sql only if it is. Each commits
    on its own (Firebird makes DDL usable only once committed).
    """

    applied: Callable[[Connection], bool]
    apply_sql: str
    undo_sql: str

    def apply(self, conn: Connection) -> None:
        if not self.applied(conn):
            conn.execute(text(self.apply_sql))
            conn.commit()

    def undo(self, conn: Connection) -> None:
        if self.applied(conn):
            conn.execute(text(self.undo_sql))
            conn.commit()
