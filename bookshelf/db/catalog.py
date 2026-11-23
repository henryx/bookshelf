"""Lookups in Firebird's system tables: whether a schema object exists."""

from firebird.driver import Connection

__all__ = ["column_exists", "column_nullable", "constraint_exists", "table_exists"]


def table_exists(conn: Connection, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = ?", (name,))
        return cur.fetchone() is not None


def column_exists(conn: Connection, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?",
            (table, column),
        )
        return cur.fetchone() is not None


def column_nullable(conn: Connection, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT RDB$NULL_FLAG FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?",
            (table, column),
        )
        (null_flag,) = cur.fetchone()
    return not null_flag


def constraint_exists(conn: Connection, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM RDB$RELATION_CONSTRAINTS WHERE RDB$CONSTRAINT_NAME = ?",
            (name,),
        )
        return cur.fetchone() is not None
