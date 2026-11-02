import pytest
from flask import Flask

from bookshelf.db import get_db
from bookshelf.db.schema import init_db


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


def test_login_failures_indexed_by_email_then_time(db_app: Flask):
    with db_app.app_context(), get_db().cursor() as cur:
        cur.execute(
            "SELECT TRIM(S.RDB$FIELD_NAME) FROM RDB$INDICES I "
            "JOIN RDB$INDEX_SEGMENTS S ON S.RDB$INDEX_NAME = I.RDB$INDEX_NAME "
            "WHERE I.RDB$RELATION_NAME = 'LOGIN_FAILURES' "
            "AND I.RDB$INDEX_NAME = 'IDX_LOGIN_FAILURES_EMAIL_AT' "
            "ORDER BY S.RDB$FIELD_POSITION"
        )
        columns = [column for (column,) in cur.fetchall()]

    assert columns == ["EMAIL", "ATTEMPTED_AT"]


def test_init_db_can_run_again(db_app: Flask):
    with db_app.app_context():
        init_db(get_db())
