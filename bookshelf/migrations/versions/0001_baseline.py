"""The 1.0.0 schema: domains, tables, constraints and indexes.

Revision ID: 0001
Revises:
Create Date: 2026-11-24

Creates whatever of the frozen 1.0.0 schema (bookshelf/db/baseline.py) is
missing, so it serves both a new database and a 1.0.x one. A database from
before Alembic is stamped with the revision matching its SCHEMA_VERSION
instead (init_db). Never undone: a failed run leaves what it created, and
the next one creates the rest.
"""

from alembic import op

from bookshelf.db.baseline import create_missing

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_missing(op.get_bind())


def downgrade() -> None:
    pass
