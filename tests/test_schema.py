import pytest
from flask import Flask
from raw_db import BACKEND, RawConnection, raw, table_columns

from bookshelf.config import DatabaseBackend
from bookshelf.db import get_engine, get_session
from bookshelf.db.baseline import CONSTRAINTS, TABLES
from bookshelf.db.orm import Base
from bookshelf.db.schema import UnknownSearchLanguageError, init_db
from bookshelf.models.books import NewBook, add_book, list_books
from bookshelf.models.users import create_user


def _rows(
    conn: RawConnection, sql: dict[DatabaseBackend, str], *params: object
) -> list[tuple[object, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql[BACKEND], params)
        return cur.fetchall()


def _columns(conn: RawConnection, table: str) -> list[str]:
    return table_columns(conn, table)


def _domain(conn: RawConnection, table: str, column: str) -> str:
    """The column's domain; PostgreSQL's columns have its UUID type instead."""
    ((domain,),) = _rows(
        conn,
        {
            DatabaseBackend.FIREBIRD: (
                "SELECT TRIM(RDB$FIELD_SOURCE) FROM RDB$RELATION_FIELDS "
                "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?"
            ),
            DatabaseBackend.POSTGRESQL: (
                "SELECT UPPER(udt_name) FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = LOWER(?) AND UPPER(column_name) = ?"
            ),
        },
        table,
        column,
    )
    return str(domain)


def _nullable(conn: RawConnection, table: str, column: str) -> bool:
    ((not_null,),) = _rows(
        conn,
        {
            DatabaseBackend.FIREBIRD: (
                "SELECT COALESCE(RDB$NULL_FLAG, 0) FROM RDB$RELATION_FIELDS "
                "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?"
            ),
            DatabaseBackend.POSTGRESQL: (
                "SELECT CASE is_nullable WHEN 'NO' THEN 1 ELSE 0 END "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = LOWER(?) AND UPPER(column_name) = ?"
            ),
        },
        table,
        column,
    )
    return not not_null


def _constraints(conn: RawConnection, table: str) -> dict[str, str]:
    """Named constraints of a table: name -> type (Firebird's own NOT NULL ones aside)."""
    rows = _rows(
        conn,
        {
            DatabaseBackend.FIREBIRD: (
                "SELECT TRIM(RDB$CONSTRAINT_NAME), TRIM(RDB$CONSTRAINT_TYPE) "
                "FROM RDB$RELATION_CONSTRAINTS "
                "WHERE RDB$RELATION_NAME = ? AND RDB$CONSTRAINT_TYPE <> 'NOT NULL'"
            ),
            DatabaseBackend.POSTGRESQL: (
                "SELECT UPPER(c.conname), CASE c.contype "
                "WHEN 'p' THEN 'PRIMARY KEY' WHEN 'u' THEN 'UNIQUE' "
                "WHEN 'c' THEN 'CHECK' WHEN 'f' THEN 'FOREIGN KEY' END "
                "FROM pg_catalog.pg_constraint c "
                "JOIN pg_catalog.pg_class t ON t.oid = c.conrelid "
                "WHERE t.relnamespace = current_schema()::regnamespace "
                "AND t.relname = LOWER(?) AND c.contype IN ('p', 'u', 'c', 'f')"
            ),
        },
        table,
    )
    return {str(name): str(kind) for name, kind in rows}


def _reference(conn: RawConnection, constraint: str) -> tuple[object, ...] | None:
    """
    (column, referenced table, delete rule) of a foreign key. PostgreSQL's
    default rule, NO ACTION, refuses the delete as Firebird's RESTRICT does.
    """
    rows = _rows(
        conn,
        {
            DatabaseBackend.FIREBIRD: (
                "SELECT TRIM(s.RDB$FIELD_NAME), TRIM(pk.RDB$RELATION_NAME), "
                "TRIM(r.RDB$DELETE_RULE) "
                "FROM RDB$RELATION_CONSTRAINTS fk "
                "JOIN RDB$REF_CONSTRAINTS r "
                "ON r.RDB$CONSTRAINT_NAME = fk.RDB$CONSTRAINT_NAME "
                "JOIN RDB$RELATION_CONSTRAINTS pk "
                "ON pk.RDB$CONSTRAINT_NAME = r.RDB$CONST_NAME_UQ "
                "JOIN RDB$INDEX_SEGMENTS s ON s.RDB$INDEX_NAME = fk.RDB$INDEX_NAME "
                "WHERE fk.RDB$CONSTRAINT_NAME = ?"
            ),
            DatabaseBackend.POSTGRESQL: (
                "SELECT UPPER(a.attname), UPPER(r.relname), "
                "CASE c.confdeltype WHEN 'c' THEN 'CASCADE' ELSE 'RESTRICT' END "
                "FROM pg_catalog.pg_constraint c "
                "JOIN pg_catalog.pg_class r ON r.oid = c.confrelid "
                "JOIN pg_catalog.pg_attribute a "
                "ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                "WHERE c.connamespace = current_schema()::regnamespace "
                "AND c.conname = LOWER(?)"
            ),
        },
        constraint,
    )
    return rows[0] if rows else None


def _index_columns(conn: RawConnection, index: str) -> list[str]:
    rows = _rows(
        conn,
        {
            DatabaseBackend.FIREBIRD: (
                "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$INDEX_SEGMENTS "
                "WHERE RDB$INDEX_NAME = ? ORDER BY RDB$FIELD_POSITION"
            ),
            DatabaseBackend.POSTGRESQL: (
                "SELECT UPPER(a.attname) FROM pg_catalog.pg_index x "
                "JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid "
                "CROSS JOIN LATERAL UNNEST(x.indkey) WITH ORDINALITY AS k(attnum, n) "
                "JOIN pg_catalog.pg_attribute a "
                "ON a.attrelid = x.indrelid AND a.attnum = k.attnum "
                "WHERE i.relnamespace = current_schema()::regnamespace "
                "AND i.relname = LOWER(?) ORDER BY k.n"
            ),
        },
        index,
    )
    return [str(column) for (column,) in rows]


def _assert_complete_schema(conn: RawConnection) -> None:
    assert _columns(conn, "SETTINGS") == ["NAME", "VALUE"]
    assert not _nullable(conn, "SETTINGS", "VALUE")
    assert _constraints(conn, "SETTINGS") == {"PK_SETTINGS": "PRIMARY KEY"}

    assert _columns(conn, "USERS") == [
        "ID",
        "EMAIL",
        "PASSWORD_HASH",
        "ROLE",
        "IS_LOCKED",
        "NAME",
        "SURNAME",
        "AUTH_METHOD",
        "GOOGLE_SUBJECT",
        "LAST_LOGIN_AT",
    ]
    assert _domain(conn, "USERS", "ID") == "UUID"
    assert _nullable(conn, "USERS", "PASSWORD_HASH")
    assert _nullable(conn, "USERS", "NAME")
    assert _nullable(conn, "USERS", "SURNAME")
    assert _nullable(conn, "USERS", "GOOGLE_SUBJECT")
    assert _nullable(conn, "USERS", "LAST_LOGIN_AT")
    assert _constraints(conn, "USERS") == {
        "PK_USERS": "PRIMARY KEY",
        "UQ_USERS_EMAIL": "UNIQUE",
        "CHK_USERS_ROLE": "CHECK",
        "CHK_USERS_EMAIL_NORMALISED": "CHECK",
        "CHK_USERS_NAME": "CHECK",
        "CHK_USERS_SURNAME": "CHECK",
        "CHK_USERS_AUTH_METHOD": "CHECK",
        "CHK_USERS_CREDENTIALS": "CHECK",
        "UQ_USERS_GOOGLE_SUBJECT": "UNIQUE",
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
        "SERIES",
        "VOLUME",
        "PLOT",
        "CREATED_AT",
    ]
    assert _domain(conn, "BOOKS", "ID") == "UUID"
    assert _constraints(conn, "BOOKS") == {
        "PK_BOOKS": "PRIMARY KEY",
        "UQ_BOOKS_ISBN": "UNIQUE",
        "CHK_BOOKS_ISBN_CODE": "CHECK",
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

    assert _columns(conn, "BOOK_CATEGORIES") == ["BOOK_ID", "CATEGORY"]
    assert _domain(conn, "BOOK_CATEGORIES", "BOOK_ID") == "UUID"
    assert _constraints(conn, "BOOK_CATEGORIES") == {
        "PK_BOOK_CATEGORIES": "PRIMARY KEY",
        "CHK_BOOK_CATEGORIES_CATEGORY": "CHECK",
        "FK_BOOK_CATEGORIES_BOOK": "FOREIGN KEY",
    }
    assert _reference(conn, "FK_BOOK_CATEGORIES_BOOK") == (
        "BOOK_ID",
        "BOOKS",
        "CASCADE",
    )
    assert _index_columns(conn, "IDX_BOOK_CATEGORIES_CATEGORY") == ["CATEGORY"]

    assert _columns(conn, "COLLECTION_BOOKS") == [
        "USER_ID",
        "BOOK_ID",
        "COPIES",
        "IS_READ",
    ]
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
        "AUTHOR_FILTER",
    ]


def _function_arguments(conn: RawConnection, function: str) -> list[str]:
    """
    A function's arguments in order (Firebird's return value, position 0,
    left out); PostgreSQL's are named with a P_ prefix, left out too.
    """
    rows = _rows(
        conn,
        {
            DatabaseBackend.FIREBIRD: (
                "SELECT TRIM(RDB$ARGUMENT_NAME) FROM RDB$FUNCTION_ARGUMENTS "
                "WHERE RDB$FUNCTION_NAME = ? AND RDB$ARGUMENT_POSITION > 0 "
                "ORDER BY RDB$ARGUMENT_POSITION"
            ),
            DatabaseBackend.POSTGRESQL: (
                "SELECT SUBSTRING(UPPER(a.name) FROM 3) FROM pg_catalog.pg_proc p "
                "CROSS JOIN LATERAL UNNEST(p.proargnames) WITH ORDINALITY AS a(name, n) "
                "WHERE p.pronamespace = current_schema()::regnamespace "
                "AND p.proname = LOWER(?) ORDER BY a.n"
            ),
        },
        function,
    )
    return [str(name) for (name,) in rows]


def test_session_database_has_complete_schema(db_app: Flask):
    with db_app.app_context():
        _assert_complete_schema(raw(get_session()))


def test_init_db_creates_complete_schema_in_empty_database(empty_db_app: Flask):
    with empty_db_app.app_context():
        init_db(get_engine())

        _assert_complete_schema(raw(get_session()))


def test_init_db_can_run_again(empty_db_app: Flask):
    with empty_db_app.app_context():
        init_db(get_engine())

        init_db(get_engine())

        _assert_complete_schema(raw(get_session()))


def test_orm_mapping_matches_the_schema(db_app: Flask):
    """Every mapped table and column is in the schema the migrations make."""
    with db_app.app_context():
        conn = raw(get_session())
        for table in Base.metadata.sorted_tables:
            columns = _columns(conn, table.name.upper())
            assert sorted(c.name.upper() for c in table.columns) == sorted(columns)


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


def _add_book_and_search(password: str, *searches: str) -> list[list[str]]:
    """Add a user and "The Running Man", then search the catalogue."""
    session = get_session()
    user = create_user(session, "reader@example.com", password)
    add_book(session, user.id, NewBook("The Running Man"))
    return [
        [book.title for book in list_books(session, user.id, search).books]
        for search in searches
    ]


@pytest.mark.postgresql
def test_init_db_records_the_search_language(empty_db_app: Flask, password: str):
    """Without stemming ("simple"), "runs" no longer finds "Running"."""
    with empty_db_app.app_context():
        init_db(get_engine(), search_language="simple")

        assert _add_book_and_search(password, "runs", "runn") == [
            [],
            ["The Running Man"],
        ]


@pytest.mark.postgresql
def test_init_db_refuses_an_unknown_search_language(empty_db_app: Flask):
    with (
        empty_db_app.app_context(),
        pytest.raises(UnknownSearchLanguageError, match="klingon"),
    ):
        init_db(get_engine(), search_language="klingon")
