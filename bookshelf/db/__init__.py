"""
The database, on Firebird or PostgreSQL: the engines (`engine.py`), the app's
engine and sessions (`app.py`), how the models write (`writes.py`) and the
errors they handle (`errors.py`). This package exports what the app uses.
"""

from bookshelf.db.app import close_pool, get_engine, get_session, init_app
from bookshelf.db.engine import backend_of, create_db_engine
from bookshelf.db.errors import (
    is_concurrent_delete,
    is_foreign_key_violation,
    is_unique_violation,
    is_update_conflict,
)
from bookshelf.db.writes import (
    execute_change,
    read_committed,
    retry_after_concurrent_delete,
    try_change,
)

__all__ = [
    "backend_of",
    "close_pool",
    "create_db_engine",
    "execute_change",
    "get_engine",
    "get_session",
    "init_app",
    "is_concurrent_delete",
    "is_foreign_key_violation",
    "is_unique_violation",
    "is_update_conflict",
    "read_committed",
    "retry_after_concurrent_delete",
    "try_change",
]
