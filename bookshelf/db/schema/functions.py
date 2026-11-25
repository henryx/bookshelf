"""
The PSQL functions (and PostgreSQL's helpers), created or replaced on every
init_db after the revisions: their bodies can change without a revision.
"""

from sqlalchemy import Connection

from bookshelf.config import DatabaseBackend
from bookshelf.db import backend_of
from bookshelf.db.changes import Sql, statement

__all__ = ["FUNCTIONS", "POSTGRESQL_HELPERS", "create_functions"]

# PSQL functions, created after the revisions are applied. Each is created or
# replaced on every init_db, so its body can change without a revision.
FUNCTIONS: dict[str, Sql] = {
    # The book lists' search: the title, an ISBN text or any author's
    # "name surname" contains the text, ignoring case (CONTAINING always does).
    # ISBNs are searched for two texts: see isbn_searches in models/books/isbn.py.
    # CONTAINING '' is true, so an empty search matches every book. NULL
    # (no ISBN, no author) counts as no match. With AUTHOR_FILTER, only that
    # author's books match.
    # PostgreSQL searches words instead (SEARCH_MATCHES): each word of the text
    # starts a word of the title or of an author's name and surname, all of
    # them read as one text, after stemming ("running" finds "run"); ISBNs
    # still contain their texts. Its parameters are named apart from the
    # columns.
    "BOOK_MATCHES": {
        DatabaseBackend.FIREBIRD: """
        CREATE OR ALTER FUNCTION BOOK_MATCHES (
            BOOK_ID TYPE OF COLUMN BOOKS.ID,
            BOOK_TITLE TYPE OF COLUMN BOOKS.TITLE,
            BOOK_ISBN TYPE OF COLUMN BOOKS.ISBN,
            SEARCH_TEXT VARCHAR(255),
            ISBN_TEXT VARCHAR(255),
            ISBN_13_TEXT VARCHAR(255),
            AUTHOR_FILTER TYPE OF COLUMN AUTHORS.ID
        ) RETURNS BOOLEAN
        AS
        BEGIN
            RETURN (
                AUTHOR_FILTER IS NULL
                OR EXISTS (
                    SELECT 1 FROM BOOK_AUTHORS fa
                    WHERE fa.BOOK_ID = :BOOK_ID AND fa.AUTHOR_ID = :AUTHOR_FILTER
                )
            ) AND COALESCE(
                BOOK_TITLE CONTAINING SEARCH_TEXT
                OR BOOK_ISBN CONTAINING ISBN_TEXT
                OR BOOK_ISBN CONTAINING ISBN_13_TEXT
                OR EXISTS (
                    SELECT 1 FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
                    WHERE ba.BOOK_ID = :BOOK_ID
                      AND (a.NAME || ' ' || a.SURNAME) CONTAINING :SEARCH_TEXT
                ),
                FALSE
            );
        END
    """,
        DatabaseBackend.POSTGRESQL: r"""
            CREATE OR REPLACE FUNCTION BOOK_MATCHES (
                P_BOOK_ID UUID,
                P_BOOK_TITLE VARCHAR,
                P_BOOK_ISBN VARCHAR(18),
                P_SEARCH_TEXT VARCHAR,
                P_ISBN_TEXT VARCHAR,
                P_ISBN_13_TEXT VARCHAR,
                P_AUTHOR_FILTER UUID
            ) RETURNS BOOLEAN
            LANGUAGE SQL STABLE
            AS $$
                SELECT (
                    P_AUTHOR_FILTER IS NULL
                    OR EXISTS (
                        SELECT 1 FROM BOOK_AUTHORS fa
                        WHERE fa.BOOK_ID = P_BOOK_ID AND fa.AUTHOR_ID = P_AUTHOR_FILTER
                    )
                ) AND (
                    SEARCH_MATCHES(
                        P_BOOK_TITLE || ' ' || COALESCE((
                            SELECT STRING_AGG(a.NAME || ' ' || a.SURNAME, ' ')
                            FROM BOOK_AUTHORS ba JOIN AUTHORS a ON a.ID = ba.AUTHOR_ID
                            WHERE ba.BOOK_ID = P_BOOK_ID
                        ), ''),
                        P_SEARCH_TEXT
                    )
                    OR COALESCE(
                        P_BOOK_ISBN LIKE CONTAINS_PATTERN(P_ISBN_TEXT) ESCAPE '\'
                        OR P_BOOK_ISBN LIKE CONTAINS_PATTERN(P_ISBN_13_TEXT) ESCAPE '\',
                        FALSE
                    )
                )
            $$
        """,
    },
}

# PostgreSQL's helpers, created before the functions using them, in order.
POSTGRESQL_HELPERS: dict[str, str] = {
    # The text search configuration init_db recorded (APP_SEARCH_LANGUAGE).
    "SEARCH_CONFIG": r"""
        CREATE OR REPLACE FUNCTION SEARCH_CONFIG ()
        RETURNS REGCONFIG
        LANGUAGE SQL STABLE
        AS $$
            SELECT COALESCE(
                (SELECT "VALUE" FROM SETTINGS WHERE NAME = 'SEARCH_LANGUAGE'),
                'simple'
            )::REGCONFIG
        $$
    """,
    # The query for a search text: each of its words, stemmed, as the start
    # of a word (prefix matching, so "leck" finds "Leckie"), all of them
    # required. NULL when the text has no words but stop words ("the").
    "SEARCH_QUERY": r"""
        CREATE OR REPLACE FUNCTION SEARCH_QUERY (P_TEXT VARCHAR)
        RETURNS TSQUERY
        LANGUAGE SQL STABLE
        AS $$
            SELECT TO_TSQUERY(
                'simple', STRING_AGG(QUOTE_LITERAL(word) || ':*', ' & ')
            )
            FROM UNNEST(TSVECTOR_TO_ARRAY(TO_TSVECTOR(SEARCH_CONFIG(), P_TEXT))) AS word
        $$
    """,
    # Whether a document matches a search text (SEARCH_QUERY); a text with
    # no words (empty, or only stop words) matches every document.
    "SEARCH_MATCHES": r"""
        CREATE OR REPLACE FUNCTION SEARCH_MATCHES (P_DOCUMENT VARCHAR, P_TEXT VARCHAR)
        RETURNS BOOLEAN
        LANGUAGE SQL STABLE
        AS $$
            SELECT COALESCE(
                TO_TSVECTOR(SEARCH_CONFIG(), P_DOCUMENT) @@ SEARCH_QUERY(P_TEXT),
                SEARCH_QUERY(P_TEXT) IS NULL
            )
        $$
    """,
    # The LIKE pattern for "contains this text, as typed": for ISBNs.
    "CONTAINS_PATTERN": r"""
        CREATE OR REPLACE FUNCTION CONTAINS_PATTERN (P_TEXT VARCHAR)
        RETURNS VARCHAR
        LANGUAGE SQL IMMUTABLE
        AS $$
            SELECT '%' || REPLACE(REPLACE(REPLACE(P_TEXT, '\', '\\'), '%', '\%'), '_', '\_') || '%'
        $$
    """,
}


def create_functions(conn: Connection) -> None:
    functions = list(FUNCTIONS.values())
    if backend_of(conn) is DatabaseBackend.POSTGRESQL:
        functions = [*POSTGRESQL_HELPERS.values(), *functions]
    for ddl in functions:
        # As the driver's own SQL: in text(), PSQL's :VARIABLE references
        # would be taken for bound parameters. psycopg reads % as one, so
        # PostgreSQL's LIKE wildcards are doubled.
        sql = statement(conn, ddl)
        if backend_of(conn) is DatabaseBackend.POSTGRESQL:
            sql = sql.replace("%", "%%")
        conn.exec_driver_sql(sql)
    conn.commit()
