from firebird.driver import Connection

__all__ = ["init_db", "table_exists"]

# Until the first release is tagged the schema is not final: change these
# definitions in place (and recreate development databases). After it, changes
# to released tables will need their own upgrade steps.

# Created before TABLES, which use them.
DOMAINS: dict[str, str] = {
    # UUIDs stored as 16 raw bytes (uuid.UUID.bytes); UUID_TO_CHAR() shows them as text.
    "UUID": "CREATE DOMAIN UUID AS CHAR(16) CHARACTER SET OCTETS",
}

# Columns only: constraints are in CONSTRAINTS.
TABLES: dict[str, str] = {
    # NAME (the given name) and SURNAME are optional: NULL when not given.
    "USERS": """
        CREATE TABLE USERS (
            ID UUID NOT NULL,
            EMAIL VARCHAR(254) NOT NULL,
            PASSWORD_HASH VARCHAR(255) NOT NULL,
            ROLE VARCHAR(16) DEFAULT 'REGULAR' NOT NULL,
            IS_LOCKED BOOLEAN DEFAULT FALSE NOT NULL,
            NAME VARCHAR(127),
            SURNAME VARCHAR(127)
        )
    """,
    # One row per failed login, with no key of its own. ID is the user the
    # email belongs to, or NULL for an unknown email.
    "LOGIN_FAILURES": """
        CREATE TABLE LOGIN_FAILURES (
            ID UUID,
            EMAIL VARCHAR(254) NOT NULL,
            IP_ADDRESS VARCHAR(45) NOT NULL,
            ATTEMPTED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """,
    # The shared catalogue: one row per title, however many copies users own.
    # TITLE, PUBLISHER and SERIES ignore case (UNICODE_CI) when compared and sorted. ISBN
    # is 13 digits, ISBN-10 converted by the app. SERIES is free text, the series
    # the book belongs to; VOLUME is free text too ("2", "II", "3/4"), its place
    # in the series. CREATED_AT is the date added.
    "BOOKS": """
        CREATE TABLE BOOKS (
            ID UUID NOT NULL,
            TITLE VARCHAR(255) NOT NULL COLLATE UNICODE_CI,
            ISBN CHAR(13),
            PUBLISHER VARCHAR(255) COLLATE UNICODE_CI,
            PUBLICATION_YEAR SMALLINT,
            SERIES VARCHAR(255) COLLATE UNICODE_CI,
            VOLUME VARCHAR(32),
            PLOT VARCHAR(4000),
            CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """,
    # Authors are shared between books; a name and surname pair is unique,
    # ignoring case. NAME (the given name) is '' when there is only a
    # surname ("Homer"), never NULL, so the pair stays unique. 127 characters
    # each is what fits in one UNICODE_CI index key on 8 KB pages.
    "AUTHORS": """
        CREATE TABLE AUTHORS (
            ID UUID NOT NULL,
            NAME VARCHAR(127) DEFAULT '' NOT NULL COLLATE UNICODE_CI,
            SURNAME VARCHAR(127) NOT NULL COLLATE UNICODE_CI
        )
    """,
    # A book's authors, in order from 1 (POSITION is a reserved word).
    "BOOK_AUTHORS": """
        CREATE TABLE BOOK_AUTHORS (
            BOOK_ID UUID NOT NULL,
            AUTHOR_ID UUID NOT NULL,
            SORT_ORDER SMALLINT NOT NULL
        )
    """,
    # Free tags, stored on each book; unique per book, ignoring case.
    "BOOK_TAGS": """
        CREATE TABLE BOOK_TAGS (
            BOOK_ID UUID NOT NULL,
            TAG VARCHAR(64) NOT NULL COLLATE UNICODE_CI
        )
    """,
    # Each user's collection: the books they own, and how many copies.
    "COLLECTION_BOOKS": """
        CREATE TABLE COLLECTION_BOOKS (
            USER_ID UUID NOT NULL,
            BOOK_ID UUID NOT NULL,
            COPIES SMALLINT DEFAULT 1 NOT NULL
        )
    """,
}

# Named table constraints, added after TABLES in this order (a foreign key
# needs the key it references). Each is defined here only, never in CREATE
# TABLE, so every constraint has a name of our choosing.
CONSTRAINTS: dict[str, str] = {
    "PK_USERS": "ALTER TABLE USERS ADD CONSTRAINT PK_USERS PRIMARY KEY (ID)",
    "UQ_USERS_EMAIL": "ALTER TABLE USERS ADD CONSTRAINT UQ_USERS_EMAIL UNIQUE (EMAIL)",
    "CHK_USERS_ROLE": (
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_ROLE "
        "CHECK (ROLE IN ('ADMIN', 'REGULAR'))"
    ),
    # Emails are lowercase and trimmed, so UQ_USERS_EMAIL also holds ignoring
    # case. Lengths are compared because Firebird ignores trailing spaces in
    # string equality.
    "CHK_USERS_EMAIL_NORMALISED": (
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_EMAIL_NORMALISED CHECK ("
        "EMAIL = LOWER(EMAIL) AND CHAR_LENGTH(EMAIL) = CHAR_LENGTH(TRIM(EMAIL)))"
    ),
    # Non-blank and trimmed when given (see CHK_USERS_EMAIL_NORMALISED on lengths).
    "CHK_USERS_NAME": (
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_NAME CHECK (NAME IS NULL OR ("
        "NAME <> '' AND CHAR_LENGTH(NAME) = CHAR_LENGTH(TRIM(NAME))))"
    ),
    "CHK_USERS_SURNAME": (
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_SURNAME CHECK (SURNAME IS NULL OR ("
        "SURNAME <> '' AND CHAR_LENGTH(SURNAME) = CHAR_LENGTH(TRIM(SURNAME))))"
    ),
    # Deleting a user deletes their login failures.
    "FK_LOGIN_FAILURES_USER": (
        "ALTER TABLE LOGIN_FAILURES ADD CONSTRAINT FK_LOGIN_FAILURES_USER "
        "FOREIGN KEY (ID) REFERENCES USERS (ID) ON DELETE CASCADE"
    ),
    "PK_BOOKS": "ALTER TABLE BOOKS ADD CONSTRAINT PK_BOOKS PRIMARY KEY (ID)",
    # Any number of books may have no ISBN: UNIQUE ignores NULLs.
    "UQ_BOOKS_ISBN": "ALTER TABLE BOOKS ADD CONSTRAINT UQ_BOOKS_ISBN UNIQUE (ISBN)",
    "CHK_BOOKS_ISBN": (
        "ALTER TABLE BOOKS ADD CONSTRAINT CHK_BOOKS_ISBN "
        "CHECK (ISBN SIMILAR TO '[0-9]{13}')"
    ),
    # Non-blank and trimmed (see CHK_USERS_EMAIL_NORMALISED on lengths).
    "CHK_BOOKS_TITLE": (
        "ALTER TABLE BOOKS ADD CONSTRAINT CHK_BOOKS_TITLE CHECK ("
        "TITLE <> '' AND CHAR_LENGTH(TITLE) = CHAR_LENGTH(TRIM(TITLE)))"
    ),
    "PK_AUTHORS": "ALTER TABLE AUTHORS ADD CONSTRAINT PK_AUTHORS PRIMARY KEY (ID)",
    # Surname first: it also serves the book list sorted by author.
    "UQ_AUTHORS_SURNAME_NAME": (
        "ALTER TABLE AUTHORS ADD CONSTRAINT UQ_AUTHORS_SURNAME_NAME "
        "UNIQUE (SURNAME, NAME)"
    ),
    # Trimmed; may be empty.
    "CHK_AUTHORS_NAME": (
        "ALTER TABLE AUTHORS ADD CONSTRAINT CHK_AUTHORS_NAME CHECK ("
        "CHAR_LENGTH(NAME) = CHAR_LENGTH(TRIM(NAME)))"
    ),
    "CHK_AUTHORS_SURNAME": (
        "ALTER TABLE AUTHORS ADD CONSTRAINT CHK_AUTHORS_SURNAME CHECK ("
        "SURNAME <> '' AND CHAR_LENGTH(SURNAME) = CHAR_LENGTH(TRIM(SURNAME)))"
    ),
    "PK_BOOK_AUTHORS": (
        "ALTER TABLE BOOK_AUTHORS ADD CONSTRAINT PK_BOOK_AUTHORS "
        "PRIMARY KEY (BOOK_ID, AUTHOR_ID)"
    ),
    # Checked row by row, so a book's authors are replaced, not reordered in place.
    "UQ_BOOK_AUTHORS_SORT_ORDER": (
        "ALTER TABLE BOOK_AUTHORS ADD CONSTRAINT UQ_BOOK_AUTHORS_SORT_ORDER "
        "UNIQUE (BOOK_ID, SORT_ORDER)"
    ),
    "CHK_BOOK_AUTHORS_SORT_ORDER": (
        "ALTER TABLE BOOK_AUTHORS ADD CONSTRAINT CHK_BOOK_AUTHORS_SORT_ORDER "
        "CHECK (SORT_ORDER >= 1)"
    ),
    # Deleting a book deletes its author links; an author with books cannot be deleted.
    "FK_BOOK_AUTHORS_BOOK": (
        "ALTER TABLE BOOK_AUTHORS ADD CONSTRAINT FK_BOOK_AUTHORS_BOOK "
        "FOREIGN KEY (BOOK_ID) REFERENCES BOOKS (ID) ON DELETE CASCADE"
    ),
    "FK_BOOK_AUTHORS_AUTHOR": (
        "ALTER TABLE BOOK_AUTHORS ADD CONSTRAINT FK_BOOK_AUTHORS_AUTHOR "
        "FOREIGN KEY (AUTHOR_ID) REFERENCES AUTHORS (ID)"
    ),
    "PK_BOOK_TAGS": (
        "ALTER TABLE BOOK_TAGS ADD CONSTRAINT PK_BOOK_TAGS PRIMARY KEY (BOOK_ID, TAG)"
    ),
    "CHK_BOOK_TAGS_TAG": (
        "ALTER TABLE BOOK_TAGS ADD CONSTRAINT CHK_BOOK_TAGS_TAG CHECK ("
        "TAG <> '' AND CHAR_LENGTH(TAG) = CHAR_LENGTH(TRIM(TAG)))"
    ),
    "FK_BOOK_TAGS_BOOK": (
        "ALTER TABLE BOOK_TAGS ADD CONSTRAINT FK_BOOK_TAGS_BOOK "
        "FOREIGN KEY (BOOK_ID) REFERENCES BOOKS (ID) ON DELETE CASCADE"
    ),
    "PK_COLLECTION_BOOKS": (
        "ALTER TABLE COLLECTION_BOOKS ADD CONSTRAINT PK_COLLECTION_BOOKS "
        "PRIMARY KEY (USER_ID, BOOK_ID)"
    ),
    "CHK_COLLECTION_BOOKS_COPIES": (
        "ALTER TABLE COLLECTION_BOOKS ADD CONSTRAINT CHK_COLLECTION_BOOKS_COPIES "
        "CHECK (COPIES >= 1)"
    ),
    # Deleting a user deletes their collection.
    "FK_COLLECTION_BOOKS_USER": (
        "ALTER TABLE COLLECTION_BOOKS ADD CONSTRAINT FK_COLLECTION_BOOKS_USER "
        "FOREIGN KEY (USER_ID) REFERENCES USERS (ID) ON DELETE CASCADE"
    ),
    # A book in anyone's collection cannot be deleted.
    "FK_COLLECTION_BOOKS_BOOK": (
        "ALTER TABLE COLLECTION_BOOKS ADD CONSTRAINT FK_COLLECTION_BOOKS_BOOK "
        "FOREIGN KEY (BOOK_ID) REFERENCES BOOKS (ID)"
    ),
}

# Created after TABLES and CONSTRAINTS.
INDEXES: dict[str, str] = {
    # Login-failure count and clear: filter by email, then by time.
    "IDX_LOGIN_FAILURES_EMAIL_AT": (
        "CREATE INDEX IDX_LOGIN_FAILURES_EMAIL_AT ON LOGIN_FAILURES (EMAIL, ATTEMPTED_AT)"
    ),
    # Per-IP failure count: filter by IP, then by time.
    "IDX_LOGIN_FAILURES_IP_AT": (
        "CREATE INDEX IDX_LOGIN_FAILURES_IP_AT ON LOGIN_FAILURES (IP_ADDRESS, ATTEMPTED_AT)"
    ),
    # Book list sorted by title, or by date added (newest first).
    "IDX_BOOKS_TITLE": "CREATE INDEX IDX_BOOKS_TITLE ON BOOKS (TITLE)",
    # Publisher suggestions: publishers starting with the typed text.
    "IDX_BOOKS_PUBLISHER": "CREATE INDEX IDX_BOOKS_PUBLISHER ON BOOKS (PUBLISHER)",
    "IDX_BOOKS_CREATED_AT": (
        "CREATE DESCENDING INDEX IDX_BOOKS_CREATED_AT ON BOOKS (CREATED_AT)"
    ),
    # Books with a tag.
    "IDX_BOOK_TAGS_TAG": "CREATE INDEX IDX_BOOK_TAGS_TAG ON BOOK_TAGS (TAG)",
}

# PSQL functions, created after INDEXES. Each is created or replaced on every
# init_db, so its body can change without an upgrade step.
FUNCTIONS: dict[str, str] = {
    # The book lists' search: the title, an ISBN text or any author's
    # "name surname" contains the text, ignoring case (CONTAINING always does).
    # ISBNs are searched for two texts: see _isbn_searches in models/books.py.
    # CONTAINING '' is true, so an empty search matches every book. NULL
    # (no ISBN, no author) counts as no match. With AUTHOR_FILTER, only that
    # author's books match.
    "BOOK_MATCHES": """
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
}


def table_exists(conn: Connection, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = ?", (name,))
        return cur.fetchone() is not None


def init_db(conn: Connection) -> None:
    """
    Create missing domains, tables, constraints and indexes, and create or
    replace the functions. Safe to run more than once.
    """
    with conn.cursor() as cur:
        for name, ddl in DOMAINS.items():
            cur.execute("SELECT 1 FROM RDB$FIELDS WHERE RDB$FIELD_NAME = ?", (name,))
            if cur.fetchone() is None:
                cur.execute(ddl)
        for name, ddl in TABLES.items():
            if not table_exists(conn, name):
                cur.execute(ddl)
        for name, ddl in CONSTRAINTS.items():
            cur.execute(
                "SELECT 1 FROM RDB$RELATION_CONSTRAINTS WHERE RDB$CONSTRAINT_NAME = ?",
                (name,),
            )
            if cur.fetchone() is None:
                cur.execute(ddl)
        for name, ddl in INDEXES.items():
            cur.execute("SELECT 1 FROM RDB$INDICES WHERE RDB$INDEX_NAME = ?", (name,))
            if cur.fetchone() is None:
                cur.execute(ddl)
        for ddl in FUNCTIONS.values():
            cur.execute(ddl)
    conn.commit()
