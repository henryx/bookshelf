from firebird.driver import Connection
from flask import Flask

from bookshelf.db import get_db
from bookshelf.db.schema import CONSTRAINTS, TABLES, init_db


def _columns(conn: Connection, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? ORDER BY RDB$FIELD_POSITION",
            (table,),
        )
        return [column for (column,) in cur.fetchall()]


def _domain(conn: Connection, table: str, column: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$FIELD_SOURCE) FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?",
            (table, column),
        )
        (domain,) = cur.fetchone()
    return domain


def _nullable(conn: Connection, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT RDB$NULL_FLAG FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?",
            (table, column),
        )
        (null_flag,) = cur.fetchone()
    return not null_flag


def _constraints(conn: Connection, table: str) -> dict[str, str]:
    """Named constraints of a table: name -> type (Firebird's own NOT NULL ones aside)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$CONSTRAINT_NAME), TRIM(RDB$CONSTRAINT_TYPE) "
            "FROM RDB$RELATION_CONSTRAINTS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$CONSTRAINT_TYPE <> 'NOT NULL'",
            (table,),
        )
        return dict(cur.fetchall())


def _reference(conn: Connection, constraint: str) -> tuple[str, str, str] | None:
    """(column, referenced table, delete rule) of a foreign key."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(s.RDB$FIELD_NAME), TRIM(pk.RDB$RELATION_NAME), "
            "TRIM(r.RDB$DELETE_RULE) "
            "FROM RDB$RELATION_CONSTRAINTS fk "
            "JOIN RDB$REF_CONSTRAINTS r ON r.RDB$CONSTRAINT_NAME = fk.RDB$CONSTRAINT_NAME "
            "JOIN RDB$RELATION_CONSTRAINTS pk "
            "ON pk.RDB$CONSTRAINT_NAME = r.RDB$CONST_NAME_UQ "
            "JOIN RDB$INDEX_SEGMENTS s ON s.RDB$INDEX_NAME = fk.RDB$INDEX_NAME "
            "WHERE fk.RDB$CONSTRAINT_NAME = ?",
            (constraint,),
        )
        return cur.fetchone()


def _index_columns(conn: Connection, index: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$INDEX_SEGMENTS "
            "WHERE RDB$INDEX_NAME = ? ORDER BY RDB$FIELD_POSITION",
            (index,),
        )
        return [column for (column,) in cur.fetchall()]


def _assert_complete_schema(conn: Connection) -> None:
    assert _columns(conn, "USERS") == [
        "ID",
        "EMAIL",
        "PASSWORD_HASH",
        "ROLE",
        "IS_LOCKED",
    ]
    assert _domain(conn, "USERS", "ID") == "UUID"
    assert _constraints(conn, "USERS") == {
        "PK_USERS": "PRIMARY KEY",
        "UQ_USERS_EMAIL": "UNIQUE",
        "CHK_USERS_ROLE": "CHECK",
        "CHK_USERS_EMAIL_NORMALISED": "CHECK",
    }

    assert _columns(conn, "LOGIN_FAILURES") == [
        "ID",
        "EMAIL",
        "IP_ADDRESS",
        "ATTEMPTED_AT",
    ]
    assert _domain(conn, "LOGIN_FAILURES", "ID") == "UUID"
    assert _nullable(conn, "LOGIN_FAILURES", "ID")
    assert _constraints(conn, "LOGIN_FAILURES") == {
        "FK_LOGIN_FAILURES_USER": "FOREIGN KEY"
    }
    assert _reference(conn, "FK_LOGIN_FAILURES_USER") == ("ID", "USERS", "CASCADE")
    assert _index_columns(conn, "IDX_LOGIN_FAILURES_EMAIL_AT") == [
        "EMAIL",
        "ATTEMPTED_AT",
    ]
    assert _index_columns(conn, "IDX_LOGIN_FAILURES_IP_AT") == [
        "IP_ADDRESS",
        "ATTEMPTED_AT",
    ]


def test_session_database_has_complete_schema(db_app: Flask):
    with db_app.app_context():
        _assert_complete_schema(get_db())


def test_init_db_creates_complete_schema_in_empty_database(empty_db_app: Flask):
    with empty_db_app.app_context():
        conn = get_db()

        init_db(conn)

        _assert_complete_schema(conn)


def test_init_db_can_run_again(empty_db_app: Flask):
    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)

        init_db(conn)

        _assert_complete_schema(conn)


def test_constraints_are_kept_out_of_table_creation():
    """Named constraints live in CONSTRAINTS only; CREATE TABLE has none."""
    inline = [
        (table, keyword)
        for table, ddl in TABLES.items()
        for keyword in ("CONSTRAINT", "PRIMARY KEY", "UNIQUE", "CHECK", "REFERENCES")
        if keyword in ddl.upper()
    ]

    assert inline == []
    assert all(
        ddl.startswith("ALTER TABLE ") and f"ADD CONSTRAINT {name} " in ddl
        for name, ddl in CONSTRAINTS.items()
    )
