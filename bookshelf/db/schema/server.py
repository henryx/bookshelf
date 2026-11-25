"""What init_db needs of the server: PostgreSQL 18+, and its search language."""

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import insert

from bookshelf.config import DatabaseBackend
from bookshelf.db import backend_of
from bookshelf.db.orm import SettingRow

__all__ = [
    "UnknownSearchLanguageError",
    "UnsupportedServerError",
    "check_server",
    "set_search_language",
]


class UnsupportedServerError(RuntimeError):
    """A database server too old for this release."""


class UnknownSearchLanguageError(ValueError):
    """APP_SEARCH_LANGUAGE names no text search configuration of the server."""

    def __init__(self, language: str) -> None:
        super().__init__(
            f"PostgreSQL has no text search configuration {language!r} "
            "(APP_SEARCH_LANGUAGE): see SELECT cfgname FROM pg_ts_config"
        )
        self.language = language


def set_search_language(conn: Connection, language: str) -> None:
    """Record the searches' text search configuration (see SEARCH_CONFIG)."""
    known = conn.execute(
        text("SELECT 1 FROM pg_catalog.pg_ts_config WHERE cfgname = :name"),
        {"name": language},
    ).first()
    if known is None:
        raise UnknownSearchLanguageError(language)
    conn.execute(
        insert(SettingRow)
        .values(name="SEARCH_LANGUAGE", value=language)
        .on_conflict_do_update(
            index_elements=[SettingRow.name], set_={SettingRow.value: language}
        )
    )
    conn.commit()


# PostgreSQL 18: LIKE on a nondeterministic collation (UNICODE_CI).
MIN_POSTGRESQL_VERSION = 180000


def check_server(conn: Connection) -> None:
    if backend_of(conn) is not DatabaseBackend.POSTGRESQL:
        return
    version = int(conn.execute(text("SHOW server_version_num")).scalar_one())
    conn.rollback()
    if version < MIN_POSTGRESQL_VERSION:
        raise UnsupportedServerError(
            f"Bookshelf needs PostgreSQL 18 or newer; this server is {version}"
        )
