"""Each user's score of the books in their collection: COLLECTION_BOOKS.SCORE.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25

Applied by init_db (bookshelf/db/schema/), one change at a time: each
SchemaChange commits on its own and is skipped when already made, so
upgrade and downgrade can both run again after failing halfway. downgrade
must undo upgrade from wherever it stopped. Never change a released revision.

SCORE is the user's score of the book, 1 to 5 stars, NULL until they score
it. Nullable with no default, so the previous release's code, which never
writes it, works with it.
"""

from alembic import op

from bookshelf.db.catalog import column_exists, constraint_exists
from bookshelf.db.changes import SchemaChange

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

CHANGES: list[SchemaChange] = [
    SchemaChange(
        lambda conn: column_exists(conn, "COLLECTION_BOOKS", "SCORE"),
        "ALTER TABLE COLLECTION_BOOKS ADD SCORE SMALLINT",
        "ALTER TABLE COLLECTION_BOOKS DROP SCORE",
    ),
    SchemaChange(
        lambda conn: constraint_exists(conn, "CHK_COLLECTION_BOOKS_SCORE"),
        "ALTER TABLE COLLECTION_BOOKS ADD CONSTRAINT CHK_COLLECTION_BOOKS_SCORE "
        "CHECK (SCORE BETWEEN 1 AND 5)",
        "ALTER TABLE COLLECTION_BOOKS DROP CONSTRAINT CHK_COLLECTION_BOOKS_SCORE",
    ),
]


def upgrade() -> None:
    for change in CHANGES:
        change.apply(op.get_bind())


def downgrade() -> None:
    for change in reversed(CHANGES):
        change.undo(op.get_bind())
