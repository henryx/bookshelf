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
    "USERS": """
        CREATE TABLE USERS (
            ID UUID NOT NULL,
            EMAIL VARCHAR(254) NOT NULL,
            PASSWORD_HASH VARCHAR(255) NOT NULL,
            ROLE VARCHAR(16) DEFAULT 'REGULAR' NOT NULL,
            IS_LOCKED BOOLEAN DEFAULT FALSE NOT NULL
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
    # Deleting a user deletes their login failures.
    "FK_LOGIN_FAILURES_USER": (
        "ALTER TABLE LOGIN_FAILURES ADD CONSTRAINT FK_LOGIN_FAILURES_USER "
        "FOREIGN KEY (ID) REFERENCES USERS (ID) ON DELETE CASCADE"
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
}


def table_exists(conn: Connection, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = ?", (name,))
        return cur.fetchone() is not None


def init_db(conn: Connection) -> None:
    """
    Create missing domains, tables, constraints and indexes. Safe to run more
    than once.
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
    conn.commit()
