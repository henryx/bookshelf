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
        "NAME",
        "SURNAME",
    ]
    assert _domain(conn, "USERS", "ID") == "UUID"
    assert _nullable(conn, "USERS", "NAME")
    assert _nullable(conn, "USERS", "SURNAME")
    assert _constraints(conn, "USERS") == {
        "PK_USERS": "PRIMARY KEY",
        "UQ_USERS_EMAIL": "UNIQUE",
        "CHK_USERS_ROLE": "CHECK",
        "CHK_USERS_EMAIL_NORMALISED": "CHECK",
        "CHK_USERS_NAME": "CHECK",
        "CHK_USERS_SURNAME": "CHECK",
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

    assert _columns(conn, "BOOKS") == [
        "ID",
        "TITLE",
        "ISBN",
        "PUBLISHER",
        "PUBLICATION_YEAR",
        "VOLUME",
        "NOTES",
        "CREATED_AT",
    ]
    assert _domain(conn, "BOOKS", "ID") == "UUID"
    assert _constraints(conn, "BOOKS") == {
        "PK_BOOKS": "PRIMARY KEY",
        "UQ_BOOKS_ISBN": "UNIQUE",
        "CHK_BOOKS_ISBN": "CHECK",
        "CHK_BOOKS_TITLE": "CHECK",
    }
    assert _index_columns(conn, "IDX_BOOKS_TITLE") == ["TITLE"]
    assert _index_columns(conn, "IDX_BOOKS_PUBLISHER") == ["PUBLISHER"]
    assert _index_columns(conn, "IDX_BOOKS_CREATED_AT") == ["CREATED_AT"]

    assert _columns(conn, "AUTHORS") == ["ID", "NAME", "SURNAME"]
    assert _domain(conn, "AUTHORS", "ID") == "UUID"
    assert _constraints(conn, "AUTHORS") == {
        "PK_AUTHORS": "PRIMARY KEY",
        "UQ_AUTHORS_SURNAME_NAME": "UNIQUE",
        "CHK_AUTHORS_NAME": "CHECK",
        "CHK_AUTHORS_SURNAME": "CHECK",
    }

    assert _columns(conn, "BOOK_AUTHORS") == ["BOOK_ID", "AUTHOR_ID", "SORT_ORDER"]
    assert _domain(conn, "BOOK_AUTHORS", "BOOK_ID") == "UUID"
    assert _domain(conn, "BOOK_AUTHORS", "AUTHOR_ID") == "UUID"
    assert _constraints(conn, "BOOK_AUTHORS") == {
        "PK_BOOK_AUTHORS": "PRIMARY KEY",
        "UQ_BOOK_AUTHORS_SORT_ORDER": "UNIQUE",
        "CHK_BOOK_AUTHORS_SORT_ORDER": "CHECK",
        "FK_BOOK_AUTHORS_BOOK": "FOREIGN KEY",
        "FK_BOOK_AUTHORS_AUTHOR": "FOREIGN KEY",
    }
    assert _reference(conn, "FK_BOOK_AUTHORS_BOOK") == ("BOOK_ID", "BOOKS", "CASCADE")
    assert _reference(conn, "FK_BOOK_AUTHORS_AUTHOR") == (
        "AUTHOR_ID",
        "AUTHORS",
        "RESTRICT",
    )

    assert _columns(conn, "BOOK_TAGS") == ["BOOK_ID", "TAG"]
    assert _domain(conn, "BOOK_TAGS", "BOOK_ID") == "UUID"
    assert _constraints(conn, "BOOK_TAGS") == {
        "PK_BOOK_TAGS": "PRIMARY KEY",
        "CHK_BOOK_TAGS_TAG": "CHECK",
        "FK_BOOK_TAGS_BOOK": "FOREIGN KEY",
    }
    assert _reference(conn, "FK_BOOK_TAGS_BOOK") == ("BOOK_ID", "BOOKS", "CASCADE")
    assert _index_columns(conn, "IDX_BOOK_TAGS_TAG") == ["TAG"]

    assert _columns(conn, "COLLECTION_BOOKS") == ["USER_ID", "BOOK_ID", "COPIES"]
    assert _domain(conn, "COLLECTION_BOOKS", "USER_ID") == "UUID"
    assert _domain(conn, "COLLECTION_BOOKS", "BOOK_ID") == "UUID"
    assert _constraints(conn, "COLLECTION_BOOKS") == {
        "PK_COLLECTION_BOOKS": "PRIMARY KEY",
        "CHK_COLLECTION_BOOKS_COPIES": "CHECK",
        "FK_COLLECTION_BOOKS_USER": "FOREIGN KEY",
        "FK_COLLECTION_BOOKS_BOOK": "FOREIGN KEY",
    }
    assert _reference(conn, "FK_COLLECTION_BOOKS_USER") == (
        "USER_ID",
        "USERS",
        "CASCADE",
    )
    assert _reference(conn, "FK_COLLECTION_BOOKS_BOOK") == (
        "BOOK_ID",
        "BOOKS",
        "RESTRICT",
    )

    assert _function_arguments(conn, "BOOK_MATCHES") == [
        "BOOK_ID",
        "BOOK_TITLE",
        "BOOK_ISBN",
        "SEARCH_TEXT",
        "ISBN_TEXT",
        "ISBN_13_TEXT",
    ]


def _function_arguments(conn: Connection, function: str) -> list[str]:
    """A PSQL function's arguments in order, its return value (position 0) left out."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$ARGUMENT_NAME) FROM RDB$FUNCTION_ARGUMENTS "
            "WHERE RDB$FUNCTION_NAME = ? AND RDB$ARGUMENT_POSITION > 0 "
            "ORDER BY RDB$ARGUMENT_POSITION",
            (function,),
        )
        return [name for (name,) in cur.fetchall()]


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
