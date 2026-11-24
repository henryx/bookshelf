"""When each user last logged in: USERS.LAST_LOGIN_AT.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-25

Applied by init_db (bookshelf/db/schema.py), one change at a time: each
SchemaChange commits on its own and is skipped when already made, so
upgrade and downgrade can both run again after failing halfway. downgrade
must undo upgrade from wherever it stopped. Never change a released revision.

LAST_LOGIN_AT is the time, in UTC, of the user's last successful login (with
a password or Google), NULL until their first. Nullable with no default, so
the previous release's code, which never writes it, works with it.
"""

from alembic import op

from bookshelf.db.catalog import column_exists
from bookshelf.db.changes import SchemaChange

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CHANGES: list[SchemaChange] = [
    SchemaChange(
        lambda conn: column_exists(conn, "USERS", "LAST_LOGIN_AT"),
        "ALTER TABLE USERS ADD LAST_LOGIN_AT TIMESTAMP",
        "ALTER TABLE USERS DROP LAST_LOGIN_AT",
    ),
]


def upgrade() -> None:
    for change in CHANGES:
        change.apply(op.get_bind())


def downgrade() -> None:
    for change in reversed(CHANGES):
        change.undo(op.get_bind())
