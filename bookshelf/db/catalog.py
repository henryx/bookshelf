"""Lookups in Firebird's system tables: whether a schema object exists."""

from sqlalchemy import Connection, text

__all__ = [
    "column_exists",
    "column_nullable",
    "constraint_exists",
    "domain_exists",
    "index_exists",
    "table_exists",
]


def table_exists(conn: Connection, name: str) -> bool:
    return (
        conn.execute(
            text("SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = :name"),
            {"name": name},
        ).first()
        is not None
    )


def column_exists(conn: Connection, table: str, column: str) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM RDB$RELATION_FIELDS "
                "WHERE RDB$RELATION_NAME = :table AND RDB$FIELD_NAME = :column"
            ),
            {"table": table, "column": column},
        ).first()
        is not None
    )


def column_nullable(conn: Connection, table: str, column: str) -> bool:
    null_flag = conn.execute(
        text(
            "SELECT RDB$NULL_FLAG FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = :table AND RDB$FIELD_NAME = :column"
        ),
        {"table": table, "column": column},
    ).scalar_one()
    return not null_flag


def constraint_exists(conn: Connection, name: str) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM RDB$RELATION_CONSTRAINTS "
                "WHERE RDB$CONSTRAINT_NAME = :name"
            ),
            {"name": name},
        ).first()
        is not None
    )


def domain_exists(conn: Connection, name: str) -> bool:
    return (
        conn.execute(
            text("SELECT 1 FROM RDB$FIELDS WHERE RDB$FIELD_NAME = :name"),
            {"name": name},
        ).first()
        is not None
    )


def index_exists(conn: Connection, name: str) -> bool:
    return (
        conn.execute(
            text("SELECT 1 FROM RDB$INDICES WHERE RDB$INDEX_NAME = :name"),
            {"name": name},
        ).first()
        is not None
    )
