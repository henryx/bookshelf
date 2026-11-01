from firebird.driver import Connection, connect
from flask import Flask, current_app, g

__all__ = ["get_db", "init_app"]


def get_db() -> Connection:
    """
    Return the Firebird connection for the current app context, opening it if needed.
    """
    if "db" not in g:
        cfg = current_app.config
        g.db = connect(
            cfg["DATABASE_DSN"],
            user=cfg["DATABASE_USER"],
            password=cfg["DATABASE_PASSWORD"],
            charset="UTF8",
        )
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
