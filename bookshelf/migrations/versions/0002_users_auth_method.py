"""How each user logs in: a password (LOCAL) or a Google account (GOOGLE).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24

Upgrade step 1 of releases 1.2.0 to 1.4.x, unchanged: a database at
SCHEMA_VERSION 1 is stamped with this revision.
"""

from alembic import op

from bookshelf.db.catalog import column_exists, column_nullable, constraint_exists
from bookshelf.db.changes import SchemaChange

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Step 1 (1.2.0): how each user logs in. AUTH_METHOD is LOCAL (a password,
# the default, so existing users keep theirs) or GOOGLE (no password:
# PASSWORD_HASH becomes nullable). GOOGLE_SUBJECT is the Google account a
# Google user is bound to on their first login (Google's "sub", at most 255
# characters), NULL until then; unique, ignoring NULLs. Applied in order,
# undone in reverse.
CHANGES: list[SchemaChange] = [
    SchemaChange(
        lambda conn: column_exists(conn, "USERS", "AUTH_METHOD"),
        "ALTER TABLE USERS ADD AUTH_METHOD VARCHAR(16) DEFAULT 'LOCAL' NOT NULL",
        "ALTER TABLE USERS DROP AUTH_METHOD",
    ),
    SchemaChange(
        lambda conn: column_exists(conn, "USERS", "GOOGLE_SUBJECT"),
        "ALTER TABLE USERS ADD GOOGLE_SUBJECT VARCHAR(255)",
        "ALTER TABLE USERS DROP GOOGLE_SUBJECT",
    ),
    # Undone only while no user lacks a password: before any Google user,
    # which is when a failed step 1 is rolled back.
    SchemaChange(
        lambda conn: column_nullable(conn, "USERS", "PASSWORD_HASH"),
        "ALTER TABLE USERS ALTER PASSWORD_HASH DROP NOT NULL",
        "ALTER TABLE USERS ALTER PASSWORD_HASH SET NOT NULL",
    ),
    SchemaChange(
        lambda conn: constraint_exists(conn, "CHK_USERS_AUTH_METHOD"),
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_AUTH_METHOD "
        "CHECK (AUTH_METHOD IN ('LOCAL', 'GOOGLE'))",
        "ALTER TABLE USERS DROP CONSTRAINT CHK_USERS_AUTH_METHOD",
    ),
    # A local user has a password and no Google account; a Google user no password.
    SchemaChange(
        lambda conn: constraint_exists(conn, "CHK_USERS_CREDENTIALS"),
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_CREDENTIALS CHECK ("
        "(AUTH_METHOD = 'LOCAL' AND PASSWORD_HASH IS NOT NULL AND GOOGLE_SUBJECT IS NULL) "
        "OR (AUTH_METHOD = 'GOOGLE' AND PASSWORD_HASH IS NULL))",
        "ALTER TABLE USERS DROP CONSTRAINT CHK_USERS_CREDENTIALS",
    ),
    SchemaChange(
        lambda conn: constraint_exists(conn, "UQ_USERS_GOOGLE_SUBJECT"),
        "ALTER TABLE USERS ADD CONSTRAINT UQ_USERS_GOOGLE_SUBJECT UNIQUE (GOOGLE_SUBJECT)",
        "ALTER TABLE USERS DROP CONSTRAINT UQ_USERS_GOOGLE_SUBJECT",
    ),
]


def upgrade() -> None:
    for change in CHANGES:
        change.apply(op.get_bind())


def downgrade() -> None:
    for change in reversed(CHANGES):
        change.undo(op.get_bind())
