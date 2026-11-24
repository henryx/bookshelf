"""Migrations: init_db takes a database to the latest revision."""

from collections.abc import Callable
from typing import cast
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import Flask
from raw_db import RawConnection, raw
from sqlalchemy import Connection as SAConnection
from sqlalchemy import bindparam, text

from bookshelf.db import get_engine, get_session
from bookshelf.db.catalog import column_exists
from bookshelf.db.orm import UUIDType
from bookshelf.db.schema import (
    MIGRATIONS,
    SchemaTooNewError,
    init_db,
    schema_behind,
)

type Migrations = Callable[..., None]


class AddNote:
    """A revision adding BOOKS.NOTE and filling it: two commits, as Firebird needs."""

    def upgrade(self, conn: SAConnection) -> None:
        if not column_exists(conn, "BOOKS", "NOTE"):
            conn.execute(text("ALTER TABLE BOOKS ADD NOTE VARCHAR(16)"))
            conn.commit()
        conn.execute(text("UPDATE BOOKS SET NOTE = 'noted' WHERE NOTE IS NULL"))

    def rollback(self, conn: SAConnection) -> None:
        if column_exists(conn, "BOOKS", "NOTE"):
            conn.execute(text("ALTER TABLE BOOKS DROP NOTE"))
            conn.commit()


class Step:
    """
    A revision adding BOOKS.VERSE, recording its calls; it fails after adding the
    column while `fail_upgrade` is set, and before undoing anything while
    `fail_rollback` is set.
    """

    def __init__(self, *, fail_upgrade: bool = False, fail_rollback: bool = False):
        self.fail_upgrade = fail_upgrade
        self.fail_rollback = fail_rollback
        self.calls: list[str] = []

    def upgrade(self, conn: SAConnection) -> None:
        self.calls.append("upgrade")
        if not column_exists(conn, "BOOKS", "VERSE"):
            conn.execute(text("ALTER TABLE BOOKS ADD VERSE VARCHAR(16)"))
            conn.commit()
        if self.fail_upgrade:
            raise RuntimeError("upgrade failed")

    def rollback(self, conn: SAConnection) -> None:
        self.calls.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback failed")
        if column_exists(conn, "BOOKS", "VERSE"):
            conn.execute(text("ALTER TABLE BOOKS DROP VERSE"))
            conn.commit()


class AddBookThenFail:
    """A revision whose uncommitted work must not outlive its failure."""

    def upgrade(self, conn: SAConnection) -> None:
        conn.execute(
            text("INSERT INTO BOOKS (ID, TITLE) VALUES (:id, 'Dune')").bindparams(
                bindparam("id", uuid4(), type_=UUIDType())
            )
        )
        raise RuntimeError("upgrade failed")

    def rollback(self, conn: SAConnection) -> None:
        pass


def _conn() -> RawConnection:
    """The app context's connection, in a new transaction: it sees what init_db did."""
    session = get_session()
    session.rollback()
    return raw(session)


def _behind() -> bool:
    session = get_session()
    session.rollback()
    return schema_behind(session.connection())


def _setting(conn: RawConnection, name: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute('SELECT "VALUE" FROM SETTINGS WHERE NAME = ?', (name,))
        row = cur.fetchone()
    return cast(str, row[0]) if row else None


def _state() -> tuple[list[str], str | None]:
    """The revisions the database is at, and the one in progress (None when none)."""
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("SELECT TRIM(VERSION_NUM) FROM ALEMBIC_VERSION")
        revisions = [cast(str, revision) for (revision,) in cur.fetchall()]
    return revisions, _setting(conn, "SCHEMA_UPGRADE_STEP")


def _run(*statements: str) -> None:
    conn = _conn()
    with conn.cursor() as cur:
        for sql in statements:
            cur.execute(sql)
            # Firebird makes DDL usable only once committed.
            conn.commit()


def _has_column(table: str, column: str) -> bool:
    session = get_session()
    session.rollback()
    return column_exists(session.connection(), table, column)


def _notes() -> list[str | None]:
    with _conn().cursor() as cur:
        cur.execute("SELECT NOTE FROM BOOKS")
        return [cast(str | None, note) for (note,) in cur.fetchall()]


def _book_count() -> int:
    with _conn().cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM BOOKS")
        (count,) = cur.fetchone() or (0,)
    return cast(int, count)


def _add_book() -> None:
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO BOOKS (ID, TITLE) VALUES (?, 'Dune')", (uuid4(),))
    conn.commit()


def _init() -> None:
    init_db(get_engine())


# Revisions


def test_new_database_takes_every_revision(empty_db_app: Flask, migrations):
    migrations(AddNote())

    with empty_db_app.app_context():
        _init()

        assert _state() == (["t001"], None)
        assert _has_column("BOOKS", "NOTE")
        assert not _behind()


def test_each_revision_runs_once_in_order(empty_db_app: Flask, migrations):
    first, second = Step(), Step()
    migrations(first, second)

    with empty_db_app.app_context():
        _init()
        _init()

        assert (first.calls, second.calls) == (["upgrade"], ["upgrade"])
        assert _state() == (["t002"], None)


def test_only_new_revisions_run(empty_db_app: Flask, migrations):
    later = Step()
    migrations(AddNote())
    with empty_db_app.app_context():
        _init()
        _add_book()
        migrations(AddNote(), later)

        _init()

        assert later.calls == ["upgrade"]
        assert _state() == (["t002"], None)
        assert _notes() == [None]


# Rollback


def test_a_failed_revision_is_rolled_back(empty_db_app: Flask, migrations):
    failing = Step(fail_upgrade=True)
    migrations(AddNote(), failing)

    with empty_db_app.app_context():
        with pytest.raises(RuntimeError, match="upgrade failed"):
            _init()

        assert failing.calls == ["upgrade", "rollback"]
        assert not _has_column("BOOKS", "VERSE")
        assert _state() == (["t001"], None)
        assert _behind()


def test_a_revision_fixed_after_failing_applies(empty_db_app: Flask, migrations):
    step = Step(fail_upgrade=True)
    migrations(step)
    with empty_db_app.app_context():
        with pytest.raises(RuntimeError):
            _init()
        step.fail_upgrade = False

        _init()

        assert _has_column("BOOKS", "VERSE")
        assert _state() == (["t001"], None)


def test_a_failed_rollback_is_run_again_by_the_next_init_db(
    empty_db_app: Flask, migrations
):
    step = Step(fail_upgrade=True, fail_rollback=True)
    migrations(step)
    with empty_db_app.app_context():
        with pytest.raises(RuntimeError, match="rollback failed"):
            _init()
        assert _state() == (["0002"], "t001")
        assert _behind()
        step.fail_upgrade = step.fail_rollback = False
        step.calls.clear()

        _init()

        assert step.calls == ["rollback", "upgrade"]
        assert _state() == (["t001"], None)


def test_a_revision_killed_halfway_is_rolled_back_then_applied(
    empty_db_app: Flask, migrations
):
    migrations()
    with empty_db_app.app_context():
        _init()
        _add_book()
        # What a revision killed after adding its column leaves.
        _run(
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', 't001')",
            "ALTER TABLE BOOKS ADD VERSE VARCHAR(16)",
        )
        step = Step()
        migrations(step)

        _init()

        assert step.calls == ["rollback", "upgrade"]
        assert _state() == (["t001"], None)


def test_a_failed_revisions_uncommitted_work_is_discarded(
    empty_db_app: Flask, migrations
):
    migrations(AddBookThenFail())
    with empty_db_app.app_context():
        with pytest.raises(RuntimeError, match="upgrade failed"):
            _init()

        assert _book_count() == 0
        assert _state() == (["0002"], None)


# From the upgrade steps of releases before 1.5.0 (Firebird only)


@pytest.mark.firebird  # releases before 1.5.0 ran on Firebird only
def test_a_database_at_schema_version_1_is_stamped(empty_db_app: Flask, migrations):
    """Releases 1.2.0 to 1.4.x leave SCHEMA_VERSION 1: revision 0002."""
    migrations()
    with empty_db_app.app_context():
        _init()
        _run(
            "DROP TABLE ALEMBIC_VERSION",
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_VERSION', '1')",
        )
        assert _behind()
        step = Step()
        migrations(step)

        _init()

        assert _state() == (["t001"], None)
        assert step.calls == ["upgrade"]
        assert _setting(_conn(), "SCHEMA_VERSION") == "1"
        assert not _behind()


@pytest.mark.firebird  # releases before 1.5.0 ran on Firebird only
def test_a_database_without_versions_is_at_revision_0001(
    empty_db_app: Flask, migrations
):
    """A database created before versions (1.0.x) is the frozen 1.0.0 schema."""
    migrations(last="0001")
    with empty_db_app.app_context():
        _init()
        _run("DROP TABLE ALEMBIC_VERSION", "DROP TABLE SETTINGS")
        assert not _has_column("USERS", "AUTH_METHOD")
        migrations()

        _init()

        assert _state() == (["0002"], None)
        assert _has_column("USERS", "AUTH_METHOD")
        assert not _behind()


@pytest.mark.firebird  # releases before 1.5.0 ran on Firebird only
def test_a_step_left_halfway_before_alembic_is_rolled_back_then_applied(
    empty_db_app: Flask, migrations
):
    """Step 1 killed halfway by a release before 1.5.0 is revision 0002."""
    migrations(last="0001")
    with empty_db_app.app_context():
        _init()
        _run(
            "DROP TABLE ALEMBIC_VERSION",
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_VERSION', '0')",
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', '1')",
            "ALTER TABLE USERS ADD AUTH_METHOD VARCHAR(16) DEFAULT 'LOCAL' NOT NULL",
        )
        migrations()

        _init()

        assert _state() == (["0002"], None)
        assert _has_column("USERS", "GOOGLE_SUBJECT")


@pytest.mark.firebird  # releases before 1.5.0 ran on Firebird only
def test_a_schema_version_newer_than_the_code_is_refused(
    empty_db_app: Flask, migrations
):
    migrations()
    with empty_db_app.app_context():
        _init()
        _run(
            "DROP TABLE ALEMBIC_VERSION",
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_VERSION', '2')",
        )

        with pytest.raises(SchemaTooNewError, match="upgrade step 2"):
            _init()


@pytest.mark.firebird  # releases before 1.5.0 ran on Firebird only
def test_a_newer_step_left_halfway_before_alembic_is_refused(
    empty_db_app: Flask, migrations
):
    """A release before 1.5.0 with a step this one does not know: it is newer."""
    migrations()
    with empty_db_app.app_context():
        _init()
        _run(
            "DROP TABLE ALEMBIC_VERSION",
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_VERSION', '1')",
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', '2')",
        )

        with pytest.raises(SchemaTooNewError, match="upgrade step 2"):
            _init()


def test_the_baseline_is_never_undone(empty_db_app: Flask):
    """A failed run of revision 0001 keeps what it created: the next one adds the rest."""
    baseline = ScriptDirectory(str(MIGRATIONS)).get_revision("0001").module
    with empty_db_app.app_context():
        _init()
        with (
            get_engine().connect() as conn,
            Operations.context(MigrationContext.configure(conn)),
        ):
            baseline.downgrade()

        assert _has_column("BOOKS", "TITLE")


# Refusals and locking


def test_database_newer_than_the_code_is_refused(empty_db_app: Flask, migrations):
    migrations(AddNote())
    with empty_db_app.app_context():
        _init()
        migrations()

        with pytest.raises(SchemaTooNewError, match="revision t001"):
            _init()
        assert not _behind()


def test_a_newer_revision_left_halfway_is_refused(empty_db_app: Flask, migrations):
    """This release cannot roll back a revision it does not know."""
    migrations()
    with empty_db_app.app_context():
        _init()
        _run(
            "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', 't001')"
        )

        with pytest.raises(SchemaTooNewError, match="revision t001"):
            _init()
        assert _state() == (["0002"], "t001")


def test_a_second_init_db_waits_for_the_first(app: Flask, other_conn, race):
    with app.app_context():
        _init()
    # Another init_db holds the lock; it always rolls back what holds it.
    with other_conn.cursor() as cur:
        cur.execute(
            'UPDATE SETTINGS SET "VALUE" = "VALUE" WHERE NAME = \'SCHEMA_UPGRADE_LOCK\''
        )

    # Firebird waits to lock the row; PostgreSQL earlier, adding it if missing.
    race(lambda _session: _init(), "SETTINGS", commit=False)


# Requests


def test_requests_wait_while_the_schema_is_behind(app: Flask, migrations, caplog):
    """The session database has every real revision: one more puts it behind."""
    migrations(AddNote())
    client = app.test_client()

    rv = client.get("/login")

    assert rv.status_code == 503
    assert "run init-db" in rv.get_data(as_text=True)
    assert "Database schema is behind the code: run init-db" in caplog.text
    assert client.get("/static/style.css").status_code == 200


def test_requests_are_served_once_init_db_ran(empty_db_app: Flask, migrations):
    migrations(AddNote())
    with empty_db_app.app_context():
        _init()

    assert empty_db_app.test_client().get("/login").status_code == 200


def test_requests_are_served_while_the_schema_is_ahead(empty_db_app: Flask, migrations):
    """The previous release's processes, while the next one's revisions are applied."""
    migrations(AddNote())
    with empty_db_app.app_context():
        _init()
    migrations()

    assert empty_db_app.test_client().get("/login").status_code == 200
