"""
Lookups in the database's own catalog: whether a schema object exists.
Names are given as the DDL writes them, unquoted (upper case): Firebird
stores them so, PostgreSQL folds them to lower case.
"""

from sqlalchemy import Connection, TextClause, text

from bookshelf.config import DatabaseBackend
from bookshelf.db import backend_of

__all__ = [
    "collation_exists",
    "column_exists",
    "column_nullable",
    "column_type",
    "constraint_exists",
    "domain_exists",
    "index_exists",
    "table_exists",
]


def _exists(
    conn: Connection, queries: dict[DatabaseBackend, TextClause], **names: str
) -> bool:
    backend = backend_of(conn)
    if backend is DatabaseBackend.POSTGRESQL:
        names = {key: name.lower() for key, name in names.items()}
    return conn.execute(queries[backend], names).first() is not None


def table_exists(conn: Connection, name: str) -> bool:
    return _exists(
        conn,
        {
            DatabaseBackend.FIREBIRD: text(
                "SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = :name"
            ),
            DatabaseBackend.POSTGRESQL: text(
                "SELECT 1 FROM pg_catalog.pg_tables "
                "WHERE schemaname = current_schema() AND tablename = :name"
            ),
        },
        name=name,
    )


def column_exists(conn: Connection, table: str, column: str) -> bool:
    return _exists(
        conn,
        {
            DatabaseBackend.FIREBIRD: text(
                "SELECT 1 FROM RDB$RELATION_FIELDS "
                "WHERE RDB$RELATION_NAME = :table AND RDB$FIELD_NAME = :column"
            ),
            DatabaseBackend.POSTGRESQL: text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = :table AND column_name = :column"
            ),
        },
        table=table,
        column=column,
    )


def column_nullable(conn: Connection, table: str, column: str) -> bool:
    return not _exists(
        conn,
        {
            DatabaseBackend.FIREBIRD: text(
                "SELECT 1 FROM RDB$RELATION_FIELDS "
                "WHERE RDB$RELATION_NAME = :table AND RDB$FIELD_NAME = :column "
                "AND RDB$NULL_FLAG = 1"
            ),
            DatabaseBackend.POSTGRESQL: text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = :table AND column_name = :column "
                "AND is_nullable = 'NO'"
            ),
        },
        table=table,
        column=column,
    )


def constraint_exists(conn: Connection, name: str) -> bool:
    return _exists(
        conn,
        {
            DatabaseBackend.FIREBIRD: text(
                "SELECT 1 FROM RDB$RELATION_CONSTRAINTS "
                "WHERE RDB$CONSTRAINT_NAME = :name"
            ),
            DatabaseBackend.POSTGRESQL: text(
                "SELECT 1 FROM pg_catalog.pg_constraint c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = current_schema() AND c.conname = :name"
            ),
        },
        name=name,
    )


def domain_exists(conn: Connection, name: str) -> bool:
    return _exists(
        conn,
        {
            DatabaseBackend.FIREBIRD: text(
                "SELECT 1 FROM RDB$FIELDS WHERE RDB$FIELD_NAME = :name"
            ),
            DatabaseBackend.POSTGRESQL: text(
                "SELECT 1 FROM pg_catalog.pg_type t "
                "JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = current_schema() AND t.typname = :name"
            ),
        },
        name=name,
    )


def index_exists(conn: Connection, name: str) -> bool:
    return _exists(
        conn,
        {
            DatabaseBackend.FIREBIRD: text(
                "SELECT 1 FROM RDB$INDICES WHERE RDB$INDEX_NAME = :name"
            ),
            DatabaseBackend.POSTGRESQL: text(
                "SELECT 1 FROM pg_catalog.pg_indexes "
                "WHERE schemaname = current_schema() AND indexname = :name"
            ),
        },
        name=name,
    )


def collation_exists(conn: Connection, name: str) -> bool:
    """PostgreSQL only: Firebird's collations are built in."""
    return _exists(
        conn,
        {
            DatabaseBackend.FIREBIRD: text(
                "SELECT 1 FROM RDB$COLLATIONS WHERE RDB$COLLATION_NAME = :name"
            ),
            DatabaseBackend.POSTGRESQL: text(
                "SELECT 1 FROM pg_catalog.pg_collation c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.collnamespace "
                "WHERE n.nspname = current_schema() AND c.collname = :name"
            ),
        },
        name=name,
    )


def column_type(conn: Connection, table: str, column: str) -> str | None:
    """
    A text column's type as the DDL writes it, CHAR(13) or VARCHAR(18); None
    for a missing column or another type.
    """
    queries = {
        DatabaseBackend.FIREBIRD: text(
            "SELECT CASE f.RDB$FIELD_TYPE WHEN 14 THEN 'CHAR' WHEN 37 THEN 'VARCHAR' END, "
            "f.RDB$CHARACTER_LENGTH "
            "FROM RDB$RELATION_FIELDS rf "
            "JOIN RDB$FIELDS f ON f.RDB$FIELD_NAME = rf.RDB$FIELD_SOURCE "
            "WHERE rf.RDB$RELATION_NAME = :table AND rf.RDB$FIELD_NAME = :column"
        ),
        DatabaseBackend.POSTGRESQL: text(
            "SELECT CASE data_type WHEN 'character' THEN 'CHAR' "
            "WHEN 'character varying' THEN 'VARCHAR' END, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table AND column_name = :column"
        ),
    }
    backend = backend_of(conn)
    names = {"table": table, "column": column}
    if backend is DatabaseBackend.POSTGRESQL:
        names = {key: name.lower() for key, name in names.items()}
    row = conn.execute(queries[backend], names).first()
    if row is None or row[0] is None:
        return None
    kind, length = row
    return f"{str(kind).strip()}({length})"
