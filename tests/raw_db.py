"""
The tests' own SQL: setting up rows and checking what is stored, as plain
firebird-driver statements in the transaction of the app's session.
"""

from firebird.driver import Connection
from sqlalchemy.orm import Session


def raw(session: Session) -> Connection:
    """
    The firebird-driver connection under `session`, in its transaction:
    committed or rolled back with it (session.commit()), or on its own.
    """
    conn = session.connection().connection.dbapi_connection
    assert isinstance(conn, Connection)
    return conn
