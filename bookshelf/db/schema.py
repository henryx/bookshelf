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
            CONSTRAINT CHK_USERS_ROLE CHECK (ROLE IN ('ADMIN', 'REGULAR'))
        )
    """,
    "LOGIN_FAILURES": """
        CREATE TABLE LOGIN_FAILURES (
            ID UUID NOT NULL PRIMARY KEY,
            EMAIL VARCHAR(254) NOT NULL,
            ATTEMPTED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """,
}


def init_db(conn: Connection) -> None:
    """
    Create missing domains and tables. Safe to run more than once.
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
    conn.commit()
