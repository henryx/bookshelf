import pytest
from flask import Flask

from bookshelf.db import get_db


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
