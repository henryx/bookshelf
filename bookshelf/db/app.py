"""The app's engine, and the session of each app context."""

from flask import Flask, current_app, g
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from bookshelf.config import DatabaseBackend
from bookshelf.db.engine import create_db_engine

__all__ = ["close_pool", "get_engine", "get_session", "init_app"]

_ENGINE_KEY = "bookshelf.db_engine"
_SESSIONS_KEY = "bookshelf.db_sessions"


def init_app(app: Flask) -> None:
    cfg = app.config
    engine = create_db_engine(
        DatabaseBackend(cfg["DATABASE_BACKEND"]),
        str(cfg["DATABASE_DSN"]),
        str(cfg["DATABASE_USER"]),
        str(cfg["DATABASE_PASSWORD"]),
        pool_size=int(cfg["DATABASE_POOL_SIZE"]),
    )
    app.extensions[_ENGINE_KEY] = engine
    app.extensions[_SESSIONS_KEY] = sessionmaker(engine)
    app.teardown_appcontext(close_session)


def get_engine() -> Engine:
    """The current app's engine."""
    return current_app.extensions[_ENGINE_KEY]


def get_session() -> Session:
    """
    Return the session for the current app context, opening one if needed.
    It is closed when the context ends, rolling back what it left
    uncommitted, and its connection goes back to the pool.
    """
    if "db_session" not in g:
        g.db_session = current_app.extensions[_SESSIONS_KEY]()
    return g.db_session


def close_session(_exc: BaseException | None = None) -> None:
    session = g.pop("db_session", None)
    if session is not None:
        session.close()


def close_pool(app: Flask) -> None:
    """Close the app's idle connections."""
    app.extensions[_ENGINE_KEY].dispose()
