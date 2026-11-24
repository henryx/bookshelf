"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Applied by init_db (bookshelf/db/schema.py), one change at a time: each
SchemaChange commits on its own and is skipped when already made, so
upgrade and downgrade can both run again after failing halfway. downgrade
must undo upgrade from wherever it stopped. Never change a released revision.
"""

from alembic import op

from bookshelf.db import catalog  # noqa: F401  (the guards of most changes)
from bookshelf.db.changes import SchemaChange

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None

CHANGES: list[SchemaChange] = []


def upgrade() -> None:
    for change in CHANGES:
        change.apply(op.get_bind())


def downgrade() -> None:
    for change in reversed(CHANGES):
        change.undo(op.get_bind())
