from firebird.driver import Connection

__all__ = ["init_db"]

# Created before TABLES, which use them.
DOMAINS: dict[str, str] = {
    # UUIDs stored as 16 raw bytes (uuid.UUID.bytes); UUID_TO_CHAR() shows them as text.
    "UUID": "CREATE DOMAIN UUID AS CHAR(16) CHARACTER SET OCTETS",
}

TABLES: dict[str, str] = {
    "USERS": """
        CREATE TABLE USERS (
            ID UUID NOT NULL PRIMARY KEY,
            EMAIL VARCHAR(254) NOT NULL UNIQUE,
            PASSWORD_HASH VARCHAR(255) NOT NULL,
            ROLE VARCHAR(16) DEFAULT 'REGULAR' NOT NULL,
            IS_LOCKED BOOLEAN DEFAULT FALSE NOT NULL,
            CONSTRAINT CHK_USERS_ROLE CHECK (ROLE IN ('ADMIN', 'REGULAR')),
            -- Lowercase and trimmed, so UNIQUE also holds ignoring case. Lengths are
            -- compared because Firebird ignores trailing spaces in string equality.
            CONSTRAINT CHK_USERS_EMAIL_NORMALISED CHECK (
                EMAIL = LOWER(EMAIL) AND CHAR_LENGTH(EMAIL) = CHAR_LENGTH(TRIM(EMAIL))
            )
        )
    """,
    "LOGIN_FAILURES": """
        CREATE TABLE LOGIN_FAILURES (
            ID UUID NOT NULL PRIMARY KEY,
            EMAIL VARCHAR(254) NOT NULL,
            IP_ADDRESS VARCHAR(45) DEFAULT '' NOT NULL,
            ATTEMPTED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """,
}

# Columns added after their table was first released, keyed by (table, column).
# Created after TABLES, for databases whose tables predate them.
COLUMNS: dict[tuple[str, str], str] = {
    ("LOGIN_FAILURES", "IP_ADDRESS"): (
        "ALTER TABLE LOGIN_FAILURES ADD IP_ADDRESS VARCHAR(45) DEFAULT '' NOT NULL"
    ),
}

# Constraints added after their table was first released. Firebird does not
# check existing rows when one is added.
CONSTRAINTS: dict[str, str] = {
    "CHK_USERS_EMAIL_NORMALISED": (
        "ALTER TABLE USERS ADD CONSTRAINT CHK_USERS_EMAIL_NORMALISED CHECK ("
        "EMAIL = LOWER(EMAIL) AND CHAR_LENGTH(EMAIL) = CHAR_LENGTH(TRIM(EMAIL)))"
    ),
}

# Created after TABLES and COLUMNS, which they index.
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


def init_db(conn: Connection) -> None:
    """
    Create missing domains, tables, columns, constraints and indexes.
    Safe to run more than once.
    """
    with conn.cursor() as cur:
        for name, ddl in DOMAINS.items():
            cur.execute("SELECT 1 FROM RDB$FIELDS WHERE RDB$FIELD_NAME = ?", (name,))
            if cur.fetchone() is None:
                cur.execute(ddl)
        for name, ddl in TABLES.items():
            cur.execute(
                "SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = ?", (name,)
            )
            if cur.fetchone() is None:
                cur.execute(ddl)
        for (table, column), ddl in COLUMNS.items():
            cur.execute(
                "SELECT 1 FROM RDB$RELATION_FIELDS "
                "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?",
                (table, column),
            )
            if cur.fetchone() is None:
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
