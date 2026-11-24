"""Serials' codes in BOOKS.ISBN: an ISSN's EAN-13 (977…) and its add-on.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24

Applied by init_db (bookshelf/db/schema.py), one change at a time: each
SchemaChange commits on its own and is skipped when already made, so
upgrade and downgrade can both run again after failing halfway. downgrade
must undo upgrade from wherever it stopped. Never change a released revision.

BOOKS.ISBN holds an ISBN-13 (978/979 + 10 digits), as before, or a serial's
barcode: 977 + 10 digits, then maybe its 2- or 5-digit add-on (usually the
issue). It widens to VARCHAR(18) (the old release's codes all still fit and
pass; on Firebird they lose the CHAR's padding), and CHK_BOOKS_ISBN (13
digits) gives way to CHK_BOOKS_ISBN_CODE.
UQ_BOOKS_ISBN stays: each code at most once, so a serial's issues are told
apart by their add-on. Firebird retypes no column a key uses, so it is
dropped for the type change and added again (KeyedChange): the upgrade
fails, and is rolled back, if a duplicate ISBN is written meanwhile.
Downgrade is for a failed upgrade: it restores CHK_BOOKS_ISBN and keeps the
wider column (Firebird narrows none; the previous release works with it).
"""

from alembic import op
from sqlalchemy import Connection, text

from bookshelf.config import DatabaseBackend
from bookshelf.db.catalog import column_type, constraint_exists
from bookshelf.db.changes import Key, KeyedChange, SchemaChange

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Firebird keeps a CHAR's padding when it becomes a VARCHAR: the ISBNs had 5
# trailing spaces. (PostgreSQL drops them itself.) Done on downgrade too: the
# wider column stays.
TRIM_PADDING = SchemaChange(
    lambda conn: not _padded_isbns(conn),
    "UPDATE BOOKS SET ISBN = TRIM(ISBN) "
    "WHERE CHAR_LENGTH(ISBN) <> CHAR_LENGTH(TRIM(ISBN))",
    None,
)

CHANGES: list[SchemaChange | KeyedChange] = [
    # Undone before the column narrows back (see downgrade), done before it widens.
    SchemaChange(
        lambda conn: not constraint_exists(conn, "CHK_BOOKS_ISBN"),
        "ALTER TABLE BOOKS DROP CONSTRAINT CHK_BOOKS_ISBN",
        "ALTER TABLE BOOKS ADD CONSTRAINT CHK_BOOKS_ISBN "
        "CHECK (ISBN SIMILAR TO '[0-9]{13}')",
    ),
    # Firebird retypes no column a key uses: UQ_BOOKS_ISBN is dropped meanwhile.
    KeyedChange(
        SchemaChange(
            lambda conn: column_type(conn, "BOOKS", "ISBN") == "VARCHAR(18)",
            {
                DatabaseBackend.FIREBIRD: "ALTER TABLE BOOKS ALTER ISBN TYPE VARCHAR(18)",
                DatabaseBackend.POSTGRESQL: (
                    "ALTER TABLE BOOKS ALTER COLUMN ISBN TYPE VARCHAR(18)"
                ),
            },
            # Kept when undone: Firebird narrows no column, and the previous
            # release works with the wider one (CHK_BOOKS_ISBN keeps it to 13).
            None,
        ),
        (
            Key(
                "UQ_BOOKS_ISBN",
                "ALTER TABLE BOOKS ADD CONSTRAINT UQ_BOOKS_ISBN UNIQUE (ISBN)",
                "ALTER TABLE BOOKS DROP CONSTRAINT UQ_BOOKS_ISBN",
            ),
        ),
    ),
    TRIM_PADDING,
    SchemaChange(
        lambda conn: constraint_exists(conn, "CHK_BOOKS_ISBN_CODE"),
        "ALTER TABLE BOOKS ADD CONSTRAINT CHK_BOOKS_ISBN_CODE CHECK ("
        "ISBN SIMILAR TO '97[89][0-9]{10}' "
        "OR ISBN SIMILAR TO '977[0-9]{10}([0-9]{2}|[0-9]{5})?')",
        "ALTER TABLE BOOKS DROP CONSTRAINT CHK_BOOKS_ISBN_CODE",
    ),
]


def _padded_isbns(conn: Connection) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM BOOKS WHERE CHAR_LENGTH(ISBN) <> CHAR_LENGTH(TRIM(ISBN))"
            )
        ).first()
        is not None
    )


def upgrade() -> None:
    for change in CHANGES:
        change.apply(op.get_bind())


def downgrade() -> None:
    for change in reversed(CHANGES):
        change.undo(op.get_bind())
    TRIM_PADDING.apply(op.get_bind())
