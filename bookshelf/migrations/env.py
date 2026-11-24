"""
Alembic's environment, for `alembic revision` and `alembic history` only.
The revisions are applied by `flask init-db` (init_db in
bookshelf/db/schema.py), which commits each change on its own, rolls back a
revision that fails, and holds the lock against concurrent runs.
"""

from alembic import util

raise util.CommandError(
    "Bookshelf applies its migrations with `flask init-db`, not `alembic upgrade`"
)
