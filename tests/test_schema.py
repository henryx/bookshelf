import pytest
from firebird.driver import Connection
from flask import Flask

from bookshelf.db import get_db
from bookshelf.db.schema import COLUMNS, CONSTRAINTS, TABLES, init_db


@pytest.mark.parametrize("table", ["USERS", "LOGIN_FAILURES"])
def test_id_column_uses_uuid_domain(db_app: Flask, table: str):
    with db_app.app_context(), get_db().cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$FIELD_SOURCE) FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = 'ID'",
            (table,),
        )
        (domain,) = cur.fetchone()

    assert domain == "UUID"


def _index_columns(conn: Connection, index: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$INDEX_SEGMENTS "
            "WHERE RDB$INDEX_NAME = ? ORDER BY RDB$FIELD_POSITION",
            (index,),
        )
        return [column for (column,) in cur.fetchall()]


@pytest.mark.parametrize(
    ("index", "columns"),
    [
        ("IDX_LOGIN_FAILURES_EMAIL_AT", ["EMAIL", "ATTEMPTED_AT"]),
        ("IDX_LOGIN_FAILURES_IP_AT", ["IP_ADDRESS", "ATTEMPTED_AT"]),
    ],
)
def test_login_failures_indexes(db_app: Flask, index: str, columns: list[str]):
    with db_app.app_context():
        assert _index_columns(get_db(), index) == columns


def test_init_db_adds_ip_column_to_existing_login_failures(db_app: Flask):
    """Databases created before IP_ADDRESS existed get it from init-db."""
    with db_app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("DROP INDEX IDX_LOGIN_FAILURES_IP_AT")
            cur.execute("ALTER TABLE LOGIN_FAILURES DROP IP_ADDRESS")
        conn.commit()

        init_db(conn)

        assert _index_columns(conn, "IDX_LOGIN_FAILURES_IP_AT") == [
            "IP_ADDRESS",
            "ATTEMPTED_AT",
        ]


def test_init_db_adds_email_check_to_existing_users(db_app: Flask):
    """Databases created before CHK_USERS_EMAIL_NORMALISED get it from init-db."""
    with db_app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE USERS DROP CONSTRAINT CHK_USERS_EMAIL_NORMALISED")
        conn.commit()

        init_db(conn)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM RDB$RELATION_CONSTRAINTS "
                "WHERE RDB$CONSTRAINT_NAME = 'CHK_USERS_EMAIL_NORMALISED'"
            )
            assert cur.fetchone() is not None


def test_init_db_can_run_again(db_app: Flask):
    with db_app.app_context():
        init_db(get_db())


def test_later_columns_and_constraints_are_defined_once():
    """Each lives only in COLUMNS or CONSTRAINTS, never also in CREATE TABLE."""
    later = [column for _, column in COLUMNS] + list(CONSTRAINTS)
    duplicated = [
        name for name in later for ddl in TABLES.values() if name in ddl.upper()
    ]

    assert duplicated == []
